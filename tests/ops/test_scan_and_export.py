"""Level 2 scan, ops export and the ops-backed mart views (offline, DuckDB)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from copilot.audit.log import verify_chain
from copilot.config import Settings
from copilot.contracts.validation import CheckResult
from copilot.ops.export import catalog_rows, export_rows
from copilot.ops.scan import cell_status, run_scan, scan_months
from copilot.ops.schema import OPS_TABLES
from copilot.ops.sink import LocalSink
from tests.marts.local import connect, query_view

AS_OF = date(2025, 10, 15)


def _result(status, slices):
    return CheckResult.build(
        check_id="V-X-01",
        check_name="x",
        metric_id="m",
        status=status,
        principle_refs=[3],
        measured={},
        threshold={},
        affected_slices=slices,
    )


def test_cell_status_expansion():
    es_aug = _result("FAIL", [{"merchant_country": "ES", "period": "2025-08-01", "status": "FAIL"}])
    assert cell_status(es_aug, date(2025, 8, 1), "ES") == "FAIL"
    assert cell_status(es_aug, date(2025, 8, 1), "DE") == "PASS"
    assert cell_status(es_aug, date(2025, 7, 1), "ES") == "PASS"
    month_only = _result("WARN", [{"period": "2025-09-01", "status": "WARN"}])
    assert cell_status(month_only, date(2025, 9, 1), "IT") == "WARN"
    source_only = _result("WARN", [{"source": "src_transactions", "status": "WARN"}])
    assert cell_status(source_only, date(2025, 1, 1), "FR") == "WARN"
    no_slices = _result("FAIL", [])
    assert cell_status(no_slices, date(2025, 1, 1), "FR") == "FAIL"
    assert cell_status(_result("PASS", []), date(2025, 1, 1), "FR") == "PASS"
    mixed = _result(
        "FAIL",
        [
            {"merchant_country": "ES", "period": "2025-08-01", "status": "WARN"},
            {"period": "2025-08-01", "status": "FAIL"},
        ],
    )
    assert cell_status(mixed, date(2025, 8, 1), "ES") == "FAIL"


@pytest.fixture(scope="module")
def scan(small_default, pack, tmp_path_factory):
    settings = replace(
        Settings(),
        backend="duckdb",
        parquet_dir=small_default.parquet_dir,
        runs_dir=tmp_path_factory.mktemp("runs"),
    )
    out = run_scan(settings, pack, AS_OF, metrics=["chargeback_rate_value", "settled_volume_eur"])
    return settings, out


def test_scan_grid_shape_and_audit(scan, pack):
    settings, out = scan
    months = scan_months(pack)
    countries = pack.pack.dimensions["merchant_country"].values
    n_checks = sum(len(r.checks) for r in out.reports)
    assert len(out.rows) == n_checks * len(months) * len(countries)
    assert {r["run_type"] for r in out.rows} == {"scan"}
    assert set(out.rows[0]) == set(OPS_TABLES["ops_check_results"].column_names)
    assert all(r["measured"] is None for r in out.rows if r["status"] == "PASS")
    assert verify_chain(settings.runs_dir / out.run_id).ok


def test_scan_finds_the_planted_defects(scan):
    _, out = scan
    fails = {
        (r["metric_id"], r["check_id"], r["period_month"], r["merchant_country"])
        for r in out.rows
        if r["status"] == "FAIL"
    }
    assert ("chargeback_rate_value", "V-PK-01", "2025-08-01", "ES") in fails  # D1
    assert ("chargeback_rate_value", "V-PK-01", "2025-08-01", "DE") not in fails
    sept = {f[1] for f in fails if f[2] == "2025-09-01"}
    assert {"V-TIME-01", "V-COMP-01"} <= sept  # D3, D2
    assert not [f for f in fails if f[2] <= "2025-07-01" and f[1] != "V-PK-01"]


def test_ops_views_on_scan_rows(scan, small_default, pack, tmp_path):
    _, out = scan
    sink = LocalSink(tmp_path)
    sink.write("ops_check_results", out.rows, "append", out.run_id)
    # an older scan for the same as-of must be ignored by the marts
    old = [
        {**r, "run_id": "scan-old", "recorded_at": "2020-01-01T00:00:00+00:00", "status": "FAIL"}
        for r in out.rows[:50]
    ]
    sink.write("ops_check_results", old, "append", "scan-old")
    con = connect(small_default.parquet_dir, {"ops_check_results": sink.read("ops_check_results")})
    dq = query_view(con, pack, "mart_dq_status")
    assert set(dq["run_id"]) == {out.run_id} and len(dq) == len(out.rows)
    card = query_view(con, pack, "mart_bcbs239_scorecard")
    assert set(card["principle_name"].dropna()) >= {"Accuracy and integrity", "Completeness"}
    assert (card["pass"] + card["warn"] + card["fail"] == card["checks_total"]).all()
    p3 = card[card["principle_no"] == 3].iloc[0]
    assert 0 < p3["pass_rate"] < 1


def _fake_run(runs_dir, rid, status="DELIVERED", deadline=None):
    d = runs_dir / rid
    (d / "payloads").mkdir(parents=True)
    gates = [
        {
            "gate": "G1",
            "decision": "approve",
            "decision_id": f"{rid}:G1:1",
            "actor": "a",
            "role": "requester",
            "ts": "2026-01-05T10:00:00Z",
            "acknowledged_warnings": [],
        },
        {
            "gate": "G2",
            "decision": "independent_path",
            "decision_id": f"{rid}:G2:1",
            "actor": "b",
            "role": "approver",
            "ts": "2026-01-05T10:30:00Z",
            "acknowledged_warnings": [],
        },
        {
            "gate": "G3",
            "decision": "approve",
            "decision_id": f"{rid}:G3:1",
            "actor": "b",
            "role": "approver",
            "ts": "2026-01-05T11:00:00Z",
            "acknowledged_warnings": ["m:V-MAT-01"],
        },
    ]
    manifest = {
        "request_id": rid,
        "status": status,
        "pack_version": "1.2.0",
        "backend": "duckdb",
        "source_path": "independent",
        "spec": {"title": "Rückfrage → Chargeback rate", "deadline": deadline},
        "llm": {"calls": 2, "cost_usd": 0.01, "tokens_in": 100, "tokens_out": 10},
        "gate_decisions": gates,
        "accepted_findings": [{"check_id": "V-PK-01"}],
        "query_runs": [{"bytes_processed": 1000}, {"bytes_processed": 24}],
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    (d / "payloads" / "0001-llm_call.json").write_text(json.dumps({"replayed": True}), "utf-8")
    events = [
        {"ts": "2026-01-05T09:59:00+00:00", "event_type": "request_received"},
        {
            "ts": "2026-01-05T10:01:00+00:00",
            "event_type": "llm_call",
            "payload_ref": "payloads/0001-llm_call.json",
        },
        {"ts": "2026-01-05T11:00:00+00:00", "event_type": "status_changed"},
    ]
    (d / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), "utf-8")


def test_export_rows_from_run_folders(tmp_path, pack, small_default):
    _fake_run(tmp_path, "req-1", deadline="2026-01-06")
    _fake_run(tmp_path, "req-2", status="REJECTED")
    (tmp_path / "scan-x").mkdir()  # a scan folder has no manifest and is skipped
    rows = export_rows(tmp_path, pack)
    runs = {r["request_id"]: r for r in rows["ops_runs"]}
    assert set(runs) == {"req-1", "req-2"}
    r1 = runs["req-1"]
    assert r1["title"] == "Rückfrage → Chargeback rate"
    assert r1["duration_s"] == 3660 and r1["g2_escalations"] == 1 and r1["accepted_findings"] == 1
    assert r1["bytes_processed"] == 1024 and r1["llm_replayed"] is True
    assert r1["delivered_at"].startswith("2026-01-05T11:00")
    assert runs["req-2"]["delivered_at"] is None
    assert len(rows["ops_gate_decisions"]) == 6
    for table, data in rows.items():
        cols = set(OPS_TABLES[table].column_names)
        assert all(set(r) <= cols for r in data), table

    con = connect(small_default.parquet_dir, rows)
    req = query_view(con, pack, "mart_requests").set_index("request_id")
    assert bool(req.loc["req-1", "on_time"]) is True
    assert pd.isna(req.loc["req-2", "on_time"])  # no deadline: not assessed
    gd = query_view(con, pack, "mart_gate_decisions")
    assert sorted(gd["acknowledged_warnings"].tolist()) == [0, 0, 0, 0, 1, 1]


def test_metric_catalog_governance_flags(pack):
    cat = {r["metric_id"]: r for r in catalog_rows(pack)}
    assert set(cat) == set(pack.metrics)
    assert cat["chargeback_rate_value"]["has_independent_path"] is True
    # both paths read the same raw tables: not an independent path (see the metric limitation)
    assert cat["top10_merchant_concentration"]["has_independent_path"] is False
    assert all(r["lineage_documented"] for r in cat.values())
    assert cat["chargeback_rate_value"]["reconciles_to"] == "ledger_control_totals"


def test_local_sink_append_and_replace(tmp_path):
    sink = LocalSink(tmp_path)
    row = {"metric_id": "m", "version": "1", "name": "Rückfrage"}
    sink.write("ops_metric_catalog", [row], "append", "x")
    sink.write("ops_metric_catalog", [row], "append", "x")
    assert len(sink.read("ops_metric_catalog")) == 2
    sink.write("ops_metric_catalog", [row], "replace", "x")
    assert sink.read("ops_metric_catalog") == [row]
    assert "Rückfrage" in sink.path("ops_metric_catalog").read_text("utf-8")
    with pytest.raises(ValueError, match="unknown columns"):
        sink.write("ops_metric_catalog", [{"bogus": 1}], "append", "x")
