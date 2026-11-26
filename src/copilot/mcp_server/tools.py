"""Governed Level 1 tools (FR-P1..P3). No tool accepts SQL.

`run_metric` compiles the metric's governed preferred template through the compiler (which
validates metric, dimensions, filter values and period alignment) and executes it through the
guarded data-access layer: same SQL policy, byte limit, timeout and labels as the workflow
(component=mcp). Metric definitions are returned without validation tolerances, check sets or
early-warning thresholds (the maker never sees thresholds, FR-A6).

Every tool call is appended to an append-only JSONL log (runs/mcp/calls.jsonl). It is
replaced by the hash-chained audit module in M4.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from copilot.contracts.validation import canonical_value
from copilot.data.session import DataSession, profile_tables
from copilot.packs.compiler import CompileError, MetricRequest, compile_metric
from copilot.packs.models import DomainPack
from copilot.packs.views import AS_OF_RULE, metric_definition_view

MAX_ROWS = 1000


def snapshot_meaning(snapshot_id: str) -> str:
    """Explain snapshot_id: the physical state of the tables read, not the business as-of."""
    if snapshot_id.startswith("live@"):
        return (
            "System time of the read: the current tables were queried at "
            f"{snapshot_id[5:]} (no per-call snapshot). It is not the business as-of date; "
            "as_of_applied is."
        )
    if snapshot_id.startswith("duckdb:"):
        return (
            "Local mode: the fixed Parquet files identified by this hash were read. It is not "
            "the business as-of date; as_of_applied is."
        )
    return (
        "Run snapshot: frozen copies of the tables taken at the start of the run. It is not "
        "the business as-of date; as_of_applied is."
    )


SessionFactory = Callable[[str], DataSession]  # request_id -> session (profile "metric")


class ToolInputError(ValueError):
    pass


class GovernedTools:
    def __init__(self, pack: DomainPack, session_factory: SessionFactory, log_dir: Path) -> None:
        self.pack = pack
        self._session_factory = session_factory
        self._log = log_dir / "calls.jsonl"

    # ---- logging --------------------------------------------------------------------------
    def _record(self, tool: str, args: dict[str, Any], outcome: dict[str, Any]) -> None:
        self._log.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": tool,
            "args": canonical_value(args),
            "pack": f"{self.pack.pack.id}@{self.pack.pack.version}",
            **canonical_value(outcome),
        }
        with self._log.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")

    # ---- catalogue ------------------------------------------------------------------------
    def list_metrics(self) -> list[dict[str, Any]]:
        out = [
            {
                "metric_id": m.id,
                "name": m.name,
                "version": m.version,
                "unit": m.unit,
                "allowed_dimensions": m.allowed_dimensions,
            }
            for m in sorted(self.pack.metrics.values(), key=lambda m: m.id)
            if m.status == "approved"
        ]
        self._record("list_metrics", {}, {"count": len(out)})
        return out

    def get_metric_definition(self, metric_id: str) -> dict[str, Any]:
        m = self.pack.metrics.get(metric_id)
        if m is None:
            raise ToolInputError(f"unknown metric {metric_id!r}")
        out = {
            **metric_definition_view(self.pack, metric_id),
            "changelog": [c.model_dump(mode="json") for c in m.changelog],
            "pack": f"{self.pack.pack.id}@{self.pack.pack.version}",
        }
        self._record("get_metric_definition", {"metric_id": metric_id}, {"found": True})
        return out

    def search_catalog(self, query: str) -> list[dict[str, str]]:
        terms = [t for t in query.lower().split() if t]
        if not terms:
            raise ToolInputError("empty query")
        hits: list[dict[str, str]] = []

        def match(*texts: str) -> bool:
            blob = " ".join(texts).lower()
            return all(t in blob for t in terms)

        for m in sorted(self.pack.metrics.values(), key=lambda m: m.id):
            if m.status == "approved" and match(m.id, m.name, m.definition, m.unit):
                hits.append({"kind": "metric", "id": m.id, "summary": m.name})
        for name, d in sorted(self.pack.pack.dimensions.items()):
            if match(name, d.description, " ".join(d.values)):
                hits.append({"kind": "dimension", "id": name, "summary": d.description})
        for name in profile_tables(self.pack, "metric"):
            src = self.pack.pack.sources[name]
            if match(name, src.description, src.layer):
                hits.append({"kind": "table", "id": name, "summary": src.description})
        self._record("search_catalog", {"query": query}, {"hits": len(hits)})
        return hits

    def describe_table(self, table: str) -> dict[str, Any]:
        if table not in profile_tables(self.pack, "metric"):
            raise ToolInputError(f"table {table!r} is not available through this server")
        session = self._session_factory(self._request_id("describe"))
        try:
            columns = session.backend.describe(session.resolver(table))
        finally:
            session.close()
        src = self.pack.pack.sources[table]
        out = {
            "table": table,
            "layer": src.layer,
            "description": src.description,
            "primary_key": src.primary_key,
            "columns": columns,
        }
        self._record("describe_table", {"table": table}, {"columns": len(columns)})
        return out

    # ---- governed execution -----------------------------------------------------------------
    def run_metric(
        self,
        metric_id: str,
        period_start: str,
        period_end: str,
        as_of: str,
        grain: str = "month",
        dims: list[str] | None = None,
        filters: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        try:
            req = MetricRequest(
                metric_id=metric_id,
                period_start=date.fromisoformat(period_start),
                period_end=date.fromisoformat(period_end),
                as_of=date.fromisoformat(as_of),
                grain=grain,  # type: ignore[arg-type]
                dims=tuple(dims or ()),
                filters={k: tuple(v) for k, v in (filters or {}).items()},
            )
        except ValueError as e:
            raise ToolInputError(str(e)) from None
        if grain not in ("month", "quarter"):
            raise ToolInputError("grain must be 'month' or 'quarter'")
        rid = self._request_id("run")
        session = self._session_factory(rid)
        try:
            try:
                q = compile_metric(self.pack, req, "preferred", session.dialect, session.resolver)  # type: ignore[arg-type]
            except CompileError as e:
                raise ToolInputError(str(e)) from None
            df, rec = session.backend.execute(q.sql, q.params)
        finally:
            session.close()
        rows = [
            {k: canonical_value(v) for k, v in r.items()}
            for r in df.head(MAX_ROWS).to_dict(orient="records")
        ]
        meta = {
            "request_id": rid,
            "metric_id": q.metric_id,
            "metric_version": q.metric_version,
            "template_id": q.template_id,
            "sql_sha256": rec.sql_sha256,
            "result_sha256": rec.result_sha256,
            "as_of_applied": req.as_of.isoformat(),
            "as_of_rule": AS_OF_RULE,
            "snapshot_id": rec.snapshot_id,
            "snapshot_meaning": snapshot_meaning(rec.snapshot_id),
            "bytes_processed": rec.bytes_processed,
            "rows": rec.rows,
            "truncated": rec.rows > MAX_ROWS,
        }
        self._record(
            "run_metric",
            {
                "metric_id": metric_id,
                "period_start": period_start,
                "period_end": period_end,
                "as_of": as_of,
                "grain": grain,
                "dims": dims or [],
                "filters": filters or {},
            },
            meta,
        )
        return {**meta, "columns": list(df.columns), "data": rows}

    @staticmethod
    def _request_id(kind: str) -> str:
        return f"mcp-{kind}-{uuid.uuid4().hex[:12]}"
