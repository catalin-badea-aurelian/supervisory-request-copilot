"""Where tile figures come from: the marts (what the dashboard shows) and a reference.

Both sources implement the same small interface, so a tile's figures are computed by the same
code from either. `MartData` reads the `spb_marts` views. `ReferenceData` does not touch the
marts: metric values are compiled from the governed independent templates and run through the
guarded session, DQ cells are rebuilt from the scan's audit log, and request figures from the
run folders (ADR 016, pre-review controls).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from copilot.contracts.validation import CheckResult
from copilot.marts.views import NON_ADDITIVE, UNIT_MULTIPLIER, components
from copilot.packs.compiler import MetricRequest, compile_metric
from copilot.packs.models import DomainPack

ALL = "ALL"
Cells = dict[tuple[str, str], float]  # (month ISO, country or ALL) -> value
TIME_STATUS = {"PARTIAL": "WARN", "INCOMPLETE": "FAIL", "NOT_LOADED": "FAIL"}
DQ_COLUMNS = ["metric_id", "check_id", "period_month", "merchant_country", "status"]


def iso(v: Any) -> str:
    return pd.Timestamp(v).date().isoformat()


class Data(Protocol):
    def cells(self, metric_id: str) -> Cells: ...
    def rag(self, metric_id: str) -> dict[tuple[str, str], tuple[str, float]]: ...
    def dq_cells(self) -> pd.DataFrame: ...
    def principle_rates(self) -> dict[int, float]: ...
    def timeliness(self) -> dict[tuple[str, str], str]: ...
    def catalog(self) -> pd.DataFrame: ...
    def requests(self) -> pd.DataFrame: ...
    def gates(self) -> pd.DataFrame: ...


def classify(value: float, amber: float, red: float, direction: str) -> str:
    if direction == "lower_is_worse":
        return "RED" if value <= red else "AMBER" if value <= amber else "GREEN"
    return "RED" if value >= red else "AMBER" if value >= amber else "GREEN"


def principle_rates_from_cells(dq: pd.DataFrame) -> dict[int, float]:
    out: dict[int, list[int]] = {}
    for refs, status in zip(dq["principle_refs"], dq["status"], strict=True):
        for p in refs:
            tot = out.setdefault(int(p), [0, 0])
            tot[0] += 1
            tot[1] += status == "PASS"
    return {p: v[1] / v[0] for p, v in sorted(out.items())}


def on_time(deadline: Any, delivered_at: Any) -> bool | None:
    if deadline is None or pd.isna(deadline):
        return None
    if delivered_at is None or pd.isna(delivered_at):
        return False
    return pd.Timestamp(delivered_at).date() <= pd.Timestamp(deadline).date()


class MartData:
    """Frames read from the marts (BigQuery or the DuckDB harness), keyed by mart name."""

    def __init__(self, frames: dict[str, pd.DataFrame], pack: DomainPack) -> None:
        self.frames = frames
        self.pack = pack

    def cells(self, metric_id: str) -> Cells:
        m = self.pack.metrics[metric_id]
        if metric_id in NON_ADDITIVE:
            df = self.frames["mart_ewi_monthly"]
            df = df[df["metric_id"] == metric_id]
            return {
                (iso(p), c): float(v)
                for p, c, v in zip(
                    df["period_month"], df["merchant_country"], df["value"], strict=True
                )
            }
        comp = self.frames["mart_ewi_components"]
        cols = list(components(self.pack))
        out: Cells = {}
        for keys, label in ((["period_month", "merchant_country"], None), (["period_month"], ALL)):
            g = comp.groupby(keys)[cols].sum().astype(float).reset_index()
            for _, r in g.iterrows():
                if m.numerator is None:
                    v = r[metric_id]
                else:
                    den = r[m.denominator]
                    v = r[m.numerator] / den * UNIT_MULTIPLIER[m.unit] if den else float("nan")
                out[(iso(r["period_month"]), label or r["merchant_country"])] = float(v)
        return out

    def rag(self, metric_id: str) -> dict[tuple[str, str], tuple[str, float]]:
        df = self.frames["mart_ewi_monthly"]
        df = df[df["metric_id"] == metric_id]
        return {
            (iso(p), c): (s, float(v))
            for p, c, s, v in zip(
                df["period_month"],
                df["merchant_country"],
                df["rag_status"],
                df["value"],
                strict=True,
            )
        }

    def dq_cells(self) -> pd.DataFrame:
        df = self.frames["mart_dq_status"]
        out = df[[*DQ_COLUMNS, "principle_refs"]].copy()
        out["period_month"] = out["period_month"].map(iso)
        return out

    def principle_rates(self) -> dict[int, float]:
        df = self.frames["mart_bcbs239_scorecard"]
        return {
            int(p): float(r)
            for p, r in zip(df["principle_no"], df["pass_rate"], strict=True)
            if pd.notna(p)
        }

    def timeliness(self) -> dict[tuple[str, str], str]:
        df = self.frames["mart_timeliness"]
        return {
            (t, iso(m)): TIME_STATUS[s]
            for t, m, s in zip(df["table_name"], df["partition_month"], df["status"], strict=True)
            if s in TIME_STATUS
        }

    def catalog(self) -> pd.DataFrame:
        return self.frames["mart_metric_catalog"][
            ["metric_id", "has_independent_path", "lineage_documented"]
        ]

    def requests(self) -> pd.DataFrame:
        return self.frames["mart_requests"]

    def gates(self) -> pd.DataFrame:
        return self.frames["mart_gate_decisions"][["request_id", "gate", "decision"]]


class ReferenceData:
    """Independent figures: governed templates, the scan's audit log and the run folders."""

    def __init__(
        self,
        pack: DomainPack,
        backend: Any,
        resolver: Any,
        scan_dir: Path,
        runs_dir: Path,
        as_of: date,
    ) -> None:
        self.pack, self.backend, self.resolver = pack, backend, resolver
        self.scan_dir, self.runs_dir, self.as_of = scan_dir, runs_dir, as_of
        self._cells: dict[str, Cells] = {}
        self._results: list[CheckResult] | None = None

    def _run(self, metric_id: str, dims: tuple[str, ...]) -> pd.DataFrame:
        r = self.pack.pack.data_range
        req = MetricRequest(metric_id, r["start"], r["end"], self.as_of, "month", dims)
        q = compile_metric(self.pack, req, "independent", self.backend.dialect, self.resolver)
        return self.backend.query(q.sql, q.params)

    def cells(self, metric_id: str) -> Cells:
        if metric_id in self._cells:
            return self._cells[metric_id]
        out: Cells = {}
        df = self._run(metric_id, ("period", "merchant_country"))
        for p, c, v in zip(df["period"], df["merchant_country"], df[metric_id], strict=True):
            out[(iso(p), c)] = float(v)
        if metric_id not in NON_ADDITIVE:
            d = self._run(metric_id, ("period",))
            for p, v in zip(d["period"], d[metric_id], strict=True):
                out[(iso(p), ALL)] = float(v)
        self._cells[metric_id] = out
        return out

    def rag(self, metric_id: str) -> dict[tuple[str, str], tuple[str, float]]:
        e = self.pack.metrics[metric_id].ewi
        if e is None:
            return {}
        unknown = self.pack.pack.unknown_country_code
        return {
            k: (classify(v, e.amber, e.red, e.direction), v)
            for k, v in self.cells(metric_id).items()
            if k[1] not in (ALL, unknown)
        }

    # -- the scan, from its audit log
    def _check_results(self) -> list[CheckResult]:
        if self._results is None:
            out = []
            log = self.scan_dir / "audit.jsonl"
            for line in log.read_text("utf-8").splitlines():
                e = json.loads(line)
                if e["event_type"] == "check_result":
                    p = json.loads((self.scan_dir / e["payload_ref"]).read_text("utf-8"))
                    out.append(CheckResult(**p))
            self._results = out
        return self._results

    def _grid(self) -> pd.DataFrame:
        from copilot.ops.scan import grid_rows, scan_months

        months = scan_months(self.pack)
        countries = list(self.pack.pack.dimensions["merchant_country"].values)
        rows = []
        for c in self._check_results():
            rows += grid_rows(
                c,
                run_id=self.scan_dir.name,
                as_of=self.as_of,
                pack=self.pack,
                months=months,
                countries=countries,
                recorded_at="",
            )
        return pd.DataFrame(rows)

    def dq_cells(self) -> pd.DataFrame:
        g = self._grid()
        return g[[*DQ_COLUMNS, "principle_refs"]].copy()

    def principle_rates(self) -> dict[int, float]:
        return principle_rates_from_cells(self.dq_cells())

    def timeliness(self) -> dict[tuple[str, str], str]:
        order = {"WARN": 1, "FAIL": 2}
        out: dict[tuple[str, str], str] = {}
        for c in self._check_results():
            if c.check_id != "V-TIME-01":
                continue
            for s in c.affected_slices:
                if s.get("status") in order and s.get("source") and s.get("period"):
                    k = (s["source"], iso(s["period"]))
                    if order[s["status"]] > order.get(out.get(k, ""), 0):
                        out[k] = s["status"]
        return out

    def catalog(self) -> pd.DataFrame:
        from copilot.ops.export import catalog_rows

        return pd.DataFrame(catalog_rows(self.pack))[
            ["metric_id", "has_independent_path", "lineage_documented"]
        ]

    def requests(self) -> pd.DataFrame:
        from copilot.ops.export import export_rows

        rows = export_rows(self.runs_dir, self.pack)["ops_runs"]
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["on_time"] = [
            on_time(d, t) for d, t in zip(df["deadline"], df["delivered_at"], strict=True)
        ]
        return df

    def gates(self) -> pd.DataFrame:
        from copilot.ops.export import export_rows

        return pd.DataFrame(
            export_rows(self.runs_dir, self.pack)["ops_gate_decisions"],
            columns=[
                "request_id",
                "gate",
                "decision",
                "decision_id",
                "actor",
                "role",
                "ts",
                "acknowledged_warnings",
            ],
        )[["request_id", "gate", "decision"]]
