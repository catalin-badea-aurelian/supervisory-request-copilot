"""GuardedBackend: policy before any backend call, byte limit at dry run, timeouts, records."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from copilot.data.guarded import BytesLimitExceeded, GuardedBackend, QueryTimeout
from copilot.data.hashing import canonical_hash
from copilot.data.policy import PolicyViolation, SqlPolicy


class FakeExecutor:
    dialect = "bigquery"

    def __init__(self, estimate: int | None = 100) -> None:
        self.estimate = estimate
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def dry_run(self, sql: str, params: dict[str, Any], labels: dict[str, str]) -> int | None:
        self.calls.append(("dry_run", sql, labels))
        return self.estimate

    def run(self, sql, params, labels, timeout_s):
        self.calls.append(("run", sql, labels))
        return pd.DataFrame({"n": [3, 1]}), 10_485_760

    def describe(self, table_ref: str) -> list[dict[str, str]]:
        return [{"name": "n", "type": "INT64"}]


def _guarded(ex: FakeExecutor, max_bytes: int = 1_000) -> GuardedBackend:
    return GuardedBackend(
        ex,
        SqlPolicy("bigquery", ["`p.spb_raw.t`"]),
        component="analyst",
        request_id="REQ-1",
        snapshot_id="bigquery:abc",
        max_bytes=max_bytes,
        timeout_s=5,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE `p.spb_raw.t`",
        "SELECT 1; SELECT 2",
        "SELECT * FROM `p.spb_ops.runs`",
        "INSERT INTO `p.spb_raw.t` (n) VALUES (1)",
    ],
)
def test_policy_violation_never_reaches_the_backend(sql: str) -> None:
    ex = FakeExecutor()
    with pytest.raises(PolicyViolation):
        _guarded(ex).query(sql)
    assert ex.calls == []


def test_byte_limit_rejects_at_dry_run_without_executing() -> None:
    ex = FakeExecutor(estimate=5_000)
    with pytest.raises(BytesLimitExceeded) as e:
        _guarded(ex, max_bytes=1_000).query("SELECT n FROM `p.spb_raw.t`")
    assert (e.value.estimated, e.value.limit) == (5_000, 1_000)
    assert [c[0] for c in ex.calls] == ["dry_run"]


def test_execute_records_hashes_labels_and_snapshot() -> None:
    ex = FakeExecutor()
    g = _guarded(ex)
    df, rec = g.execute("SELECT n FROM `p.spb_raw.t`", {"x": 1})
    assert [c[0] for c in ex.calls] == ["dry_run", "run"]
    assert all(c[2] == {"component": "analyst", "request_id": "REQ-1"} for c in ex.calls)
    assert rec.result_sha256 == canonical_hash(df)
    assert rec.snapshot_id == "bigquery:abc"
    assert (rec.estimated_bytes, rec.bytes_processed, rec.rows) == (100, 10_485_760, 2)
    assert rec.params == {"x": 1}
    assert len(rec.sql_sha256) == 64
    assert g.records == [rec]


def test_custom_label_function_is_used() -> None:
    ex = FakeExecutor()
    g = GuardedBackend(
        ex,
        SqlPolicy("bigquery", ["`p.spb_raw.t`"]),
        component="MCP",
        request_id="R 1",
        snapshot_id="live",
        max_bytes=10_000,
        timeout_s=5,
        labels_fn=lambda c, r: {"component": c.lower(), "request_id": r.lower().replace(" ", "_")},
    )
    g.query("SELECT n FROM `p.spb_raw.t`")
    assert ex.calls[0][2] == {"component": "mcp", "request_id": "r_1"}


def test_describe_only_allowlisted_tables() -> None:
    g = _guarded(FakeExecutor())
    assert g.describe("`p.spb_raw.t`")[0]["name"] == "n"
    with pytest.raises(PolicyViolation):
        g.describe("`p.spb_control.ewi_thresholds`")


def test_dialect_mismatch_is_refused() -> None:
    with pytest.raises(ValueError):
        GuardedBackend(
            FakeExecutor(),
            SqlPolicy("duckdb", []),
            component="c",
            request_id="r",
            snapshot_id="s",
            max_bytes=1,
            timeout_s=1,
        )


def test_duckdb_executor_times_out(small_default) -> None:
    from copilot.data.duckdb_backend import TABLES, ParquetExecutor
    from copilot.data.snapshots import ParquetSnapshot

    ex = ParquetExecutor(ParquetSnapshot.take(small_default.parquet_dir, TABLES))
    try:
        with pytest.raises(QueryTimeout):
            ex.run("SELECT COUNT(*) FROM range(100000000000) a, range(10) b", {}, {}, 0.3)
    finally:
        ex.close()
