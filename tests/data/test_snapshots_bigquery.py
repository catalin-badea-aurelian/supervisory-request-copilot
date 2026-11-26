"""Opt-in (-m bigquery): run snapshots of every pack source, as spb-copilot, on the live project.

Validation on the snapshot equals validation on the live tables (every status, slice and result
hash; only V-RERUN-01's sql_sha256 differs, because the SQL names the snapshot tables), and a
rerun on the stored snapshot reproduces the same report hash. The snapshots expire after one
day and are dropped at the end of the test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from copilot.config import Settings
from copilot.data.session import open_session
from copilot.packs.compiler import MetricRequest
from copilot.validation.runner import run_checks
from copilot.validation.target import prepare_target

pytestmark = pytest.mark.bigquery

REQ = MetricRequest(
    "chargeback_rate_value",
    date(2025, 7, 1),
    date(2025, 8, 31),
    date(2025, 10, 15),
    "month",
    ("period", "merchant_country"),
)


def _validate(session, pack):
    target = prepare_target(pack, REQ, session.backend, session.resolver, request_id="m3")
    return run_checks([target], pack, session.backend, session.resolver)


def _comparable(report) -> list[dict]:
    out = []
    for c in report.checks:
        d = c.model_dump()
        if isinstance(d["measured"], dict):
            d["measured"] = {k: v for k, v in d["measured"].items() if k != "sql_sha256"}
        out.append(d)
    return out


def test_run_snapshot_matches_live_and_reruns_identically(pack) -> None:
    from google.cloud import bigquery

    st = replace(Settings.from_env(), backend="bigquery", snapshot_expiration_days=1)
    rid = "m3-snapshot-acceptance"
    live = open_session(
        st, pack, request_id=rid, component="validation", profile="validation", snapshot_mode="live"
    )
    snap_session = open_session(
        st, pack, request_id=rid, component="validation", profile="validation"
    )
    snap = snap_session.snapshot
    client = snap_session.backend._ex.client  # noqa: SLF001
    try:
        assert snap is not None and snap.snapshot_id.startswith("bigquery:")
        on_live = _validate(live, pack)
        on_snap = _validate(snap_session, pack)
        assert _comparable(on_snap) == _comparable(on_live)
        assert {c.check_id for c in on_snap.fails()} == {"V-PK-01", "V-REC-01", "V-DUAL-01"}
        assert all(r.snapshot_id == snap.snapshot_id for r in snap_session.backend.records)

        rerun = open_session(
            st, pack, request_id=rid, component="validation", profile="validation", snapshot=snap
        )
        assert _validate(rerun, pack).report_sha256 == on_snap.report_sha256
    finally:
        cfg = bigquery.QueryJobConfig(labels={"component": "snapshot", "request_id": rid})
        for t in sorted(snap.mapping):
            client.query(f"DROP SNAPSHOT TABLE IF EXISTS `{snap.fq(t)}`", job_config=cfg).result()
