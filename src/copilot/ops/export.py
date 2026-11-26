"""`copilot ops export`: run telemetry, gate decisions and the metric catalogue to `spb_ops`.

The source is the run folders written by the workflow (manifest and audit log, M4) and the
domain pack. The export rebuilds `ops_runs`, `ops_gate_decisions` and `ops_metric_catalog`
in full each time (replace), so it is idempotent. Scan results are appended by `copilot dq
scan` and are not touched here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from copilot.llm.pricing import PRICING_FILE
from copilot.packs.models import DomainPack


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def eur_per_usd(path: Path = PRICING_FILE) -> float:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return float(raw["reporting_eur_per_usd"])


def run_dirs(runs_dir: Path) -> list[Path]:
    """Request runs only (folders with a manifest); scans and the MCP log are skipped."""
    if not runs_dir.is_dir():
        return []
    return sorted(p for p in runs_dir.iterdir() if (p / "manifest.json").is_file())


def run_row(run_dir: Path, rate: float, exported_at: str) -> dict[str, Any]:
    m = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    events = _events(run_dir)
    spec = m.get("spec") or {}
    llm = m.get("llm") or {}
    times = [_ts(e["ts"]) for e in events]
    gates = m.get("gate_decisions") or []
    g3 = [g for g in gates if g["gate"] == "G3" and g["decision"] == "approve"]
    delivered = _ts(g3[-1]["ts"]) if m.get("status") == "DELIVERED" and g3 else None
    replayed = []
    for e in events:
        if e["event_type"] == "llm_call" and e.get("payload_ref"):
            p = json.loads((run_dir / e["payload_ref"]).read_text("utf-8"))
            replayed.append(bool(p.get("replayed")))
    cost = llm.get("cost_usd")
    return {
        "request_id": m["request_id"],
        "title": spec.get("title"),
        "received_at": min(times).isoformat() if times else None,
        "deadline": spec.get("deadline"),
        "delivered_at": delivered.isoformat() if delivered else None,
        "status": m.get("status"),
        "pack_version": m.get("pack_version"),
        "backend": m.get("backend"),
        "source_path": m.get("source_path"),
        "llm_calls": llm.get("calls", 0),
        "llm_replayed": bool(replayed) and all(replayed),
        "tokens_in": llm.get("tokens_in", 0),
        "tokens_out": llm.get("tokens_out", 0),
        "est_cost_eur": round(cost * rate, 6) if cost is not None else None,
        "bytes_processed": sum(int(q.get("bytes_processed") or 0) for q in m.get("query_runs", [])),
        "duration_s": (max(times) - min(times)).total_seconds() if times else None,
        "g2_escalations": sum(1 for g in gates if g["gate"] == "G2"),
        "accepted_findings": len(m.get("accepted_findings") or []),
        "exported_at": exported_at,
    }


def gate_rows(run_dir: Path) -> list[dict[str, Any]]:
    m = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    return [
        {
            "request_id": m["request_id"],
            "decision_id": g.get("decision_id"),
            "gate": g["gate"],
            "decision": g["decision"],
            "actor": g.get("actor"),
            "role": g.get("role"),
            "ts": _ts(g["ts"]).isoformat(),
            "acknowledged_warnings": list(g.get("acknowledged_warnings") or []),
        }
        for g in m.get("gate_decisions") or []
    ]


def catalog_rows(pack: DomainPack) -> list[dict[str, Any]]:
    """Governance indicators per metric version.

    - `has_independent_path`: the independent template reads a different set of sources than
      the preferred one (top-10 concentration and active merchants read the same raw tables
      on both paths, so they do not count; see their limitations)
    - `lineage_documented`: both templates exist, and preferred and independent sources and
      a changelog are declared
    """
    from datetime import date

    from copilot.packs.compiler import MetricRequest, compile_metric

    p = pack.pack
    out = []
    for mid, m in sorted(pack.metrics.items()):
        req = MetricRequest(mid, p.data_range["start"], p.data_range["end"], p.default_as_of)
        srcs = {
            path: set(compile_metric(pack, req, path, "duckdb").sources)
            for path in ("preferred", "independent")
        }
        changed: date | None = max((c.date for c in m.changelog), default=None)
        root = Path(pack.root)
        lineage = (
            bool(m.preferred_source and m.independent_sources and m.changelog)
            and (root / m.sql_template).is_file()
            and (root / m.independent_sql_template).is_file()
        )
        out.append(
            {
                "metric_id": mid,
                "version": m.version,
                "name": m.name,
                "owner": m.owner,
                "status": m.status,
                "unit": m.unit,
                "has_independent_path": srcs["preferred"] != srcs["independent"],
                "lineage_documented": lineage,
                "reconciles_to": m.reconciles_to.table if m.reconciles_to else None,
                "changed_at": changed.isoformat() if changed else None,
                "pack_version": p.version,
            }
        )
    return out


def export_rows(runs_dir: Path, pack: DomainPack) -> dict[str, list[dict[str, Any]]]:
    rate = eur_per_usd()
    exported_at = datetime.now(UTC).isoformat(timespec="seconds")
    dirs = run_dirs(runs_dir)
    return {
        "ops_runs": [run_row(d, rate, exported_at) for d in dirs],
        "ops_gate_decisions": [g for d in dirs for g in gate_rows(d)],
        "ops_metric_catalog": catalog_rows(pack),
    }
