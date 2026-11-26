"""Run snapshots (Parquet manifest for local mode, BigQuery snapshot plans) and sessions."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from copilot.config import Settings
from copilot.data.duckdb_backend import TABLES, ParquetExecutor
from copilot.data.policy import PolicyViolation
from copilot.data.session import open_session, profile_tables
from copilot.data.snapshots import BigQuerySnapshot, ParquetSnapshot, SnapshotError
from copilot.packs.compiler import MetricRequest
from copilot.validation.runner import run_checks
from copilot.validation.target import prepare_target


@pytest.fixture
def parquet_copy(small_default, tmp_path):
    dst = tmp_path / "pq"
    shutil.copytree(small_default.parquet_dir, dst)
    return dst


def _append_byte(path) -> None:
    with path.open("ab") as fh:
        fh.write(b"\0")


def test_parquet_snapshot_id_is_deterministic(small_default) -> None:
    a = ParquetSnapshot.take(small_default.parquet_dir, TABLES)
    b = ParquetSnapshot.take(small_default.parquet_dir, list(reversed(TABLES)))
    assert a.snapshot_id == b.snapshot_id
    assert a.snapshot_id.startswith("duckdb:")
    assert set(a.files) == set(TABLES)


def test_parquet_snapshot_detects_a_changed_file(parquet_copy) -> None:
    snap = ParquetSnapshot.take(parquet_copy, TABLES)
    snap.verify()
    _append_byte(parquet_copy / "src_merchants.parquet")
    with pytest.raises(SnapshotError, match="src_merchants"):
        snap.verify()
    with pytest.raises(SnapshotError):
        ParquetExecutor(snap)


def test_parquet_snapshot_requires_every_file(parquet_copy) -> None:
    (parquet_copy / "src_merchants.parquet").unlink()
    with pytest.raises(SnapshotError, match="missing"):
        ParquetSnapshot.take(parquet_copy, TABLES)


def test_parquet_executor_cannot_read_other_files(small_default, tmp_path) -> None:
    other = tmp_path / "other.parquet"
    shutil.copy(small_default.parquet_dir / "src_merchants.parquet", other)
    ex = ParquetExecutor(ParquetSnapshot.take(small_default.parquet_dir, TABLES))
    try:
        assert ex.run("SELECT COUNT(*) AS n FROM src_merchants", {}, {}, 10)[0]["n"][0] > 0
        with pytest.raises(Exception, match="(?i)permission|disabled"):
            ex.run(f"SELECT * FROM read_parquet('{other.as_posix()}')", {}, {}, 10)
        with pytest.raises(Exception):  # noqa: B017 - the configuration is locked
            ex.run("SET enable_external_access = true", {}, {}, 10)
    finally:
        ex.close()


def test_bigquery_snapshot_plan(pack) -> None:
    ts = datetime(2025, 10, 15, 9, 30, 12, 345, tzinfo=UTC)
    s = BigQuerySnapshot.plan("proj", "spb", pack, "REQ-2025-0042", ts, 90)
    assert set(s.mapping) == set(pack.pack.sources)
    assert s.mapping["src_merchants"] == "src_merchants__req_2025_0042"
    assert s.dataset == "spb_snapshots"
    assert s.run_ts == "2025-10-15T09:30:12+00:00"
    assert s.expires_at == "2026-01-13T09:30:12+00:00"
    assert s.sources["grp_merchant_monthly"] == "proj.spb_group.grp_merchant_monthly"
    again = BigQuerySnapshot.plan("proj", "spb", pack, "REQ-2025-0042", ts, 90)
    assert again.snapshot_id == s.snapshot_id
    other = BigQuerySnapshot.plan("proj", "spb", pack, "REQ-2025-0043", ts, 90)
    assert other.snapshot_id != s.snapshot_id
    ddl = s.ddl()
    assert len(ddl) == len(pack.pack.sources)
    assert all(d.startswith("CREATE SNAPSHOT TABLE `proj.spb_snapshots.") for d in ddl)
    assert all("FOR SYSTEM_TIME AS OF TIMESTAMP '2025-10-15T09:30:12+00:00'" in d for d in ddl)
    assert all("expiration_timestamp = TIMESTAMP '2026-01-13T09:30:12+00:00'" in d for d in ddl)
    assert s.resolver()("src_merchants") == "`proj.spb_snapshots.src_merchants__req_2025_0042`"
    with pytest.raises(SnapshotError):
        s.resolver()("not_a_source")


def _settings(parquet_dir) -> Settings:
    return replace(Settings.from_env(), backend="duckdb", parquet_dir=parquet_dir)


def test_metric_profile_excludes_control_tables(pack, small_default) -> None:
    metric = profile_tables(pack, "metric")
    assert all(pack.pack.sources[t].layer in ("raw", "group") for t in metric)
    assert set(profile_tables(pack, "validation")) == set(pack.pack.sources)
    s = open_session(_settings(small_default.parquet_dir), pack, request_id="t", component="a")
    try:
        with pytest.raises(PolicyViolation, match="table_not_allowed"):
            s.backend.query("SELECT * FROM ledger_totals")
    finally:
        s.close()


def test_validation_through_a_guarded_session_gives_the_m2_hash(pack, small_default) -> None:
    req = MetricRequest(
        "chargeback_rate_value",
        date(2025, 7, 1),
        date(2025, 8, 31),
        date(2025, 10, 15),
        "month",
        ("period", "merchant_country"),
    )
    direct = run_checks(
        [prepare_target(pack, req, small_default.backend, request_id="S2")],
        pack,
        small_default.backend,
    )
    s = open_session(
        _settings(small_default.parquet_dir),
        pack,
        request_id="S2",
        component="validation",
        profile="validation",
    )
    try:
        guarded = run_checks(
            [prepare_target(pack, req, s.backend, s.resolver, request_id="S2")],
            pack,
            s.backend,
            s.resolver,
        )
    finally:
        s.close()
    assert guarded.report_sha256 == direct.report_sha256
    assert {c.check_id for c in guarded.fails()} == {"V-PK-01", "V-REC-01", "V-DUAL-01"}
    assert s.backend.records
    assert all(r.snapshot_id == s.snapshot_id for r in s.backend.records)


def test_rerun_with_a_stored_snapshot_fails_fast_if_data_changed(pack, parquet_copy) -> None:
    st = _settings(parquet_copy)
    first = open_session(st, pack, request_id="r", component="analyst")
    first.close()
    _append_byte(parquet_copy / "src_transactions.parquet")
    with pytest.raises(SnapshotError):
        open_session(st, pack, request_id="r", component="analyst", snapshot=first.snapshot)
