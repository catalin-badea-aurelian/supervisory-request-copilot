"""Agent-built dashboard (ADR 016): spec, renderer, reconciliation, gate GD, expected story."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date

import pytest

from copilot.audit.log import verify_chain
from copilot.config import Settings
from copilot.dashboard.build import DashboardTexts, TileText, apply_llm_captions, build_spec
from copilot.dashboard.data import MartData, ReferenceData
from copilot.dashboard.figures import tile_figures
from copilot.dashboard.load import load_duckdb
from copilot.dashboard.reconcile import reconcile
from copilot.dashboard.spec import (
    PAGE_IDS,
    SpecError,
    Thresholds,
    check_spec,
    dump_yaml,
    load_yaml,
    spec_hash,
    validate_spec,
)
from copilot.dashboard.workflow import GateError, build_dashboard, decide_gd
from copilot.llm.fake import ScriptedTransport
from copilot.llm.gateway import CallContext, Gateway, LlmEscalation, RunBudget
from copilot.ops.export import export_rows
from copilot.ops.scan import run_scan
from copilot.ops.schema import OPS_TABLES
from copilot.ops.sink import LocalSink

AS_OF = date(2025, 10, 15)
SCAN_METRICS = ["chargeback_rate_value", "settled_volume_eur", "transaction_count"]


# ---- spec ---------------------------------------------------------------------------------
def test_spec_is_valid_and_round_trips(pack):
    spec = build_spec(pack, "dash-t", "scan-t")
    assert tuple(p.id for p in spec.pages) == PAGE_IDS
    assert validate_spec(spec, pack) == []
    assert load_yaml(dump_yaml(spec)) == spec
    assert spec_hash(spec) == spec_hash(load_yaml(dump_yaml(spec)))
    assert dump_yaml(spec).startswith("# GENERATED FROM domain pack payments@")


def test_spec_rejects_what_the_pack_does_not_allow(pack):
    spec = build_spec(pack, "dash-t", "scan-t")

    def with_tile(tile_id, **update):
        pages = [
            p.model_copy(
                update={
                    "tiles": [
                        t.model_copy(update=update) if t.id == tile_id else t for t in p.tiles
                    ]
                }
            )
            for p in spec.pages
        ]
        return spec.model_copy(update={"pages": pages})

    bad_metric = with_tile("kpi_fraud_loss", metric_id="made_up_metric")
    assert any("not an approved glossary metric" in e for e in validate_spec(bad_metric, pack))
    bad_dim = with_tile("kpi_fraud_loss", dimensions=["customer_age"])
    assert any("not allowed" in e for e in validate_spec(bad_dim, pack))
    loose = Thresholds(direction="higher_is_worse", amber=0.5, red=0.9)
    assert any(
        "thresholds differ" in e
        for e in validate_spec(with_tile("kpi_fraud_loss", thresholds=loose), pack)
    )
    assert any(
        "unknown mart" in e
        for e in validate_spec(with_tile("kpi_fraud_loss", source="raw_x"), pack)
    )
    unknown_check = with_tile(
        "kpi_rec_breaks", params={"check_ids": ["V-NOPE-99"], "statuses": ["FAIL"]}
    )
    assert any("unknown check ids" in e for e in validate_spec(unknown_check, pack))
    with pytest.raises(SpecError):
        check_spec(spec.model_copy(update={"pages": spec.pages[:2]}), pack)


def test_thresholds_in_the_spec_are_the_packs(pack):
    spec = build_spec(pack, "dash-t", None)
    by_id = {t.id: t for t in spec.tiles()}
    e = pack.metrics["chargeback_rate_value"].ewi
    th = by_id["heat_chargeback_rate"].thresholds
    assert (th.amber, th.red) == (e.amber, e.red)
    assert by_id["line_settled_volume"].thresholds is None


# ---- captions (gateway, number guard) ----------------------------------------------------------
def _texts(spec, caption):
    return DashboardTexts(
        tiles=[TileText(tile_id=t.id, title=f"T {t.id}", caption=caption) for t in spec.tiles()]
    )


def test_llm_captions_change_text_only(pack):
    spec = build_spec(pack, "dash-t", "scan-t")
    gw = Gateway(
        Settings(), ScriptedTransport({"dashboard_captions": [_texts(spec, "Shows the tile.")]})
    )
    out = apply_llm_captions(spec, pack, gw, CallContext(RunBudget(100_000)))
    assert out.caption_source == "llm:dashboard_captions@1.0.0"
    assert all(t.caption == "Shows the tile." for t in out.tiles())
    strip = lambda s: [  # noqa: E731
        t.model_copy(update={"title": "", "caption": None}) for t in s.tiles()
    ]
    assert strip(out) == strip(spec)


def test_llm_captions_with_invented_numbers_are_rejected(pack):
    spec = build_spec(pack, "dash-t", "scan-t")
    gw = Gateway(
        Settings(),
        ScriptedTransport({"dashboard_captions": [_texts(spec, "Up 37.5% last month.")]}),
    )
    with pytest.raises(LlmEscalation):
        apply_llm_captions(spec, pack, gw, CallContext(RunBudget(100_000)))


# ---- end to end on the small default dataset ------------------------------------
@pytest.fixture(scope="module")
def env(small_default, pack, tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    settings = replace(
        Settings(), backend="duckdb", parquet_dir=small_default.parquet_dir, runs_dir=runs
    )
    scan = run_scan(settings, pack, AS_OF, metrics=SCAN_METRICS)
    sink = LocalSink(runs / "ops")
    sink.write("ops_check_results", scan.rows, "append", scan.run_id)
    # one delivered request run, for the operations page
    from tests.ops.test_scan_and_export import _fake_run

    _fake_run(runs, "req-1", deadline="2026-01-06")
    rows = export_rows(runs, pack)
    for table in OPS_TABLES:
        if table == "ops_check_results":
            sink.replace_where(table, "run_type", "request", rows[table], "t")
        else:
            sink.write(table, rows[table], "replace", "t")
    specs = tmp_path_factory.mktemp("specs")
    res = build_dashboard(settings, pack, scan_run_id=scan.run_id, specs_dir=specs)
    return settings, scan, res, specs


def test_build_produces_a_reconciled_candidate(env, pack):
    settings, scan, res, specs = env
    assert res.status == "PENDING_GD", res.reconciliation["mismatches"][:5]
    assert res.reconciliation["mismatch_count"] == 0
    assert res.spec_path.parent == specs and res.spec_path.name == f"dash-{scan.run_id}.yaml"
    assert verify_chain(settings.runs_dir / res.dashboard_id).ok
    types = [
        json.loads(line)["event_type"]
        for line in (settings.runs_dir / res.dashboard_id / "audit.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    assert types == ["dashboard_built", "reconciliation_completed"]


def test_html_is_self_contained_and_has_every_tile(env, pack):
    _, scan, res, _ = env
    page = res.html_path.read_text("utf-8")
    spec = load_yaml(res.spec_path.read_text("utf-8"))
    for t in spec.tiles():
        assert f'data-tile="{t.id}"' in page, t.id
    for p in spec.pages:
        assert f'id="{p.id}"' in page
    assert "Synthetic data" in page
    markup = re.sub(r"<script>.*?</script>", "", page, flags=re.S)  # the bundle has URL strings
    assert "<script src" not in page and "<link" not in page
    assert not re.search(r"""(src|href)=["']https?://""", markup), "no external resources"
    assert spec_hash(spec) in page and scan.run_id in page
    m = re.search(r"data sha256 <code>([0-9a-f]{64})</code>", page)
    assert m, "footer carries the data snapshot hash"
    assert len(page) > 1_000_000  # the plotly bundle is embedded


def test_expected_story(env, pack, small_default):
    settings, scan, res, _ = env
    spec = load_yaml(res.spec_path.read_text("utf-8"))
    tiles = {t.id: t for t in spec.tiles()}
    data = MartData(load_duckdb(settings, pack), pack)
    th = pack.metrics["chargeback_rate_value"].ewi
    # R1: IT chargeback rate in June 2025 is a red breach, and XX is never in the RAG tiles
    breaches = tile_figures(tiles["table_breaches"], data, pack)
    it = breaches["chargeback_rate_value|2025-06-01|IT"]
    assert it["rag"] == "RED" and it["value"] >= th.red
    assert not [k for k in breaches if k.endswith("|XX")]
    heat = tile_figures(tiles["heat_chargeback_rate"], data, pack)
    assert heat["2025-06-01|IT"] == pytest.approx(it["value"])
    assert not [k for k in heat if k.endswith("|XX")]
    # September: data event (timeliness, completeness) and the ES August duplicate
    matrix = tile_figures(tiles["matrix_checks"], data, pack)
    assert matrix["V-TIME-01|2025-09-01"] == "FAIL"
    assert matrix["V-PK-01|2025-08-01"] == "FAIL"
    assert matrix["V-REC-01|2025-08-01"] == "FAIL"
    findings = tile_figures(tiles["table_findings"], data, pack)
    assert findings["chargeback_rate_value|V-PK-01|2025-08-01"] == 1  # ES only
    assert tile_figures(tiles["kpi_rec_breaks"], data, pack)["count"] > 0
    assert tile_figures(tiles["bar_xx_volume"], data, pack)["2025-09-01"] > 0
    # R1 is a risk signal, not a data failure: no FAIL for IT in June
    assert "V-PK-01|2025-06-01" in matrix and matrix["V-PK-01|2025-06-01"] == "PASS"
    assert tile_figures(tiles["kpi_count"], data, pack)["value"] == 1


def test_reconciliation_catches_a_tampered_value(env, pack):
    settings, scan, res, _ = env
    frames = load_duckdb(settings, pack)
    comp = frames["mart_ewi_components"]
    mask = (comp["merchant_country"] == "IT") & (
        comp["period_month"].astype(str).str.startswith("2025-06")
    )
    comp.loc[mask, "chargeback_amount_eur"] = comp.loc[mask, "chargeback_amount_eur"] * 1.01
    spec = load_yaml(res.spec_path.read_text("utf-8"))
    from copilot.data.session import open_session

    s = open_session(
        settings, pack, request_id="t", component="dashboard", profile="metric", backend="duckdb"
    )
    try:
        ref = ReferenceData(
            pack, s.backend, s.resolver, settings.runs_dir / scan.run_id, settings.runs_dir, AS_OF
        )
        rep = reconcile(spec, MartData(frames, pack), ref, pack)
    finally:
        s.close()
    assert not rep["ok"]
    hit = {m["tile_id"] for m in rep["mismatches"]}
    assert "heat_chargeback_rate" in hit
    assert all(
        m["key"].endswith("|IT") or "ALL" in m["key"]
        for m in rep["mismatches"]
        if m["tile_id"] == "heat_chargeback_rate"
    )


# ---- gate GD ---------------------------------------------------------------------------------
def _fresh(env, pack, tmp_path):
    """A copy of the candidate in its own runs dir, so each test decides its own dashboard."""
    import shutil

    settings, _, res, _ = env
    runs = tmp_path / "runs"
    shutil.copytree(settings.runs_dir / res.dashboard_id, runs / res.dashboard_id)
    return replace(settings, runs_dir=runs), res.dashboard_id


def test_gd_blocks_publishing_without_approval(env, pack, tmp_path):
    settings, did = _fresh(env, pack, tmp_path)
    approved = tmp_path / "docs" / "dashboard"
    assert not approved.exists()
    with pytest.raises(GateError, match="reason"):
        decide_gd(settings, did, "approve", " ", approved_dir=approved)
    with pytest.raises(GateError, match="no built dashboard"):
        decide_gd(settings, "dash-unknown", "approve", "ok", approved_dir=approved)
    assert not approved.exists()
    meta = decide_gd(settings, did, "reject", "not ready", approved_dir=approved)
    assert meta["status"] == "REJECTED" and not approved.exists()
    with pytest.raises(GateError, match="already decided"):
        decide_gd(settings, did, "approve", "changed my mind", approved_dir=approved)


def test_gd_approval_publishes_and_is_audited(env, pack, tmp_path):
    settings, did = _fresh(env, pack, tmp_path)
    approved = tmp_path / "docs" / "dashboard"
    meta = decide_gd(settings, did, "approve", "figures match the marts", approved_dir=approved)
    assert meta["status"] == "APPROVED"
    assert (approved / "index.html").read_text("utf-8") == (
        settings.runs_dir / did / "dashboard" / "index.html"
    ).read_text("utf-8")
    ev = (approved / "evidence.md").read_text("utf-8")
    assert "Gate GD: **approve**" in ev and meta["spec_sha256"] in ev and "| OK |" in ev
    log = settings.runs_dir / did
    assert verify_chain(log).ok
    gd = [
        json.loads(line)
        for line in (log / "audit.jsonl").read_text("utf-8").splitlines()
        if json.loads(line)["event_type"] == "gate_decision"
    ]
    assert len(gd) == 1 and "GD approve" in gd[0]["summary"]
    payload = json.loads((log / gd[0]["payload_ref"]).read_text("utf-8"))
    assert payload["gate"] == "GD" and payload["reason"] == "figures match the marts"


def test_gd_refuses_a_blocked_or_modified_candidate(env, pack, tmp_path):
    settings, did = _fresh(env, pack, tmp_path)
    approved = tmp_path / "docs" / "dashboard"
    run = settings.runs_dir / did
    # a candidate edited after reconciliation
    page = run / "dashboard" / "index.html"
    page.write_text(page.read_text("utf-8") + "<!-- edited -->", "utf-8")
    with pytest.raises(GateError, match="changed after reconciliation"):
        decide_gd(settings, did, "approve", "ok", approved_dir=approved)
    # a reconciliation with a mismatch
    meta_path = run / "dashboard.json"
    meta = json.loads(meta_path.read_text("utf-8"))
    meta["status"] = "BLOCKED"
    meta_path.write_text(json.dumps(meta), "utf-8")
    rep = json.loads((run / "reconciliation.json").read_text("utf-8"))
    rep.update(ok=False, mismatch_count=3)
    (run / "reconciliation.json").write_text(json.dumps(rep), "utf-8")
    with pytest.raises(GateError, match="blocked: 3 tile mismatches"):
        decide_gd(settings, did, "approve", "ok", approved_dir=approved)
    assert not approved.exists()
