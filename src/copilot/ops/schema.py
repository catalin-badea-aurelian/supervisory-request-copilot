"""The `spb_ops` tables (ARCHITECTURE section 12.6): reporting copies for the dashboard.

The audit log in each run folder stays the record of truth. The copilot creates these tables
on first write (it is WRITER on `spb_ops`, so no new access is needed).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # BigQuery type
    mode: str = "NULLABLE"  # or REPEATED


@dataclass(frozen=True)
class OpsTable:
    name: str
    columns: tuple[Column, ...]
    partition_field: str | None = None
    cluster: tuple[str, ...] = ()

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def _c(spec: str) -> Column:
    name, typ, *mode = spec.split(":")
    return Column(name, typ, mode[0] if mode else "NULLABLE")


OPS_RUNS = OpsTable(
    "ops_runs",
    tuple(
        _c(s)
        for s in (
            "request_id:STRING",
            "title:STRING",
            "received_at:TIMESTAMP",
            "deadline:DATE",
            "delivered_at:TIMESTAMP",
            "status:STRING",
            "pack_version:STRING",
            "backend:STRING",
            "source_path:STRING",
            "llm_calls:INT64",
            "llm_replayed:BOOL",
            "tokens_in:INT64",
            "tokens_out:INT64",
            "est_cost_eur:FLOAT64",
            "bytes_processed:INT64",
            "duration_s:FLOAT64",
            "g2_escalations:INT64",
            "accepted_findings:INT64",
            "exported_at:TIMESTAMP",
        )
    ),
)

OPS_CHECK_RESULTS = OpsTable(
    "ops_check_results",
    tuple(
        _c(s)
        for s in (
            "run_id:STRING",
            "run_type:STRING",
            "as_of:DATE",
            "pack_version:STRING",
            "metric_id:STRING",
            "metric_version:STRING",
            "check_id:STRING",
            "check_name:STRING",
            "principle_refs:INT64:REPEATED",
            "status:STRING",
            "measured:STRING",
            "threshold:STRING",
            "period_month:DATE",
            "merchant_country:STRING",
            "accepted_ref:STRING",
            "recorded_at:TIMESTAMP",
        )
    ),
    partition_field="as_of",
    cluster=("metric_id", "check_id"),
)

OPS_GATE_DECISIONS = OpsTable(
    "ops_gate_decisions",
    tuple(
        _c(s)
        for s in (
            "request_id:STRING",
            "decision_id:STRING",
            "gate:STRING",
            "decision:STRING",
            "actor:STRING",
            "role:STRING",
            "ts:TIMESTAMP",
            "acknowledged_warnings:STRING:REPEATED",
        )
    ),
)

OPS_METRIC_CATALOG = OpsTable(
    "ops_metric_catalog",
    tuple(
        _c(s)
        for s in (
            "metric_id:STRING",
            "version:STRING",
            "name:STRING",
            "owner:STRING",
            "status:STRING",
            "unit:STRING",
            "has_independent_path:BOOL",
            "lineage_documented:BOOL",
            "reconciles_to:STRING",
            "changed_at:DATE",
            "pack_version:STRING",
        )
    ),
)

OPS_TABLES = {
    t.name: t for t in (OPS_RUNS, OPS_CHECK_RESULTS, OPS_GATE_DECISIONS, OPS_METRIC_CATALOG)
}
