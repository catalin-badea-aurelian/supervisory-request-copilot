"""The payments domain pack: shape, governance rules and stable content hash."""

from pathlib import Path

import pytest

from copilot.packs.loader import PackError, load_pack

EXPECTED_METRICS = {
    "settled_volume_eur",
    "transaction_count",
    "avg_ticket_eur",
    "chargeback_amount_eur",
    "chargeback_rate_value",
    "chargeback_rate_count",
    "fraud_loss_rate_bps",
    "refund_rate_value",
    "active_merchants",
    "top10_merchant_concentration",
}
RAW_ONLY = {
    "fraud_loss_rate_bps",
    "refund_rate_value",
    "active_merchants",
    "top10_merchant_concentration",
}


def test_glossary_is_exactly_the_initial_ten(pack) -> None:
    assert set(pack.metrics) == EXPECTED_METRICS
    assert "new_merchant_volume_share" not in pack.metrics  # S3 metric stays undefined


def test_every_metric_has_two_distinct_templates(pack) -> None:
    for m in pack.metrics.values():
        assert m.sql_template != m.independent_sql_template
        assert m.status == "approved" and m.owner and m.definition and m.changelog


def test_ewi_thresholds_match_dashboard_spec(pack) -> None:
    got = {m.id: (m.ewi.amber, m.ewi.red) for m in pack.metrics.values() if m.ewi}
    assert got == {
        "chargeback_rate_value": (0.006, 0.009),
        "fraud_loss_rate_bps": (8, 12),
        "refund_rate_value": (0.04, 0.06),
        "top10_merchant_concentration": (0.30, 0.40),
    }


def test_weaker_independence_is_documented(pack) -> None:
    changelog = (Path(pack.root) / "CHANGELOG.md").read_text("utf-8")
    for mid in RAW_ONLY:
        assert pack.metrics[mid].limitations, mid
        assert mid in changelog
    for mid in set(pack.metrics) - RAW_ONLY:
        assert not pack.metrics[mid].limitations, mid


def test_reconciling_metrics_point_at_the_ledger(pack) -> None:
    rec = {m.id for m in pack.metrics.values() if m.reconciles_to}
    assert {"settled_volume_eur", "chargeback_amount_eur", "chargeback_rate_value"} <= rec
    for mid in rec:
        assert pack.metrics[mid].reconciles_to.table == "ledger_control_totals"


def test_only_the_group_rate_metric_checks_definition_conformity(pack) -> None:
    with_def = {m.id for m in pack.metrics.values() if "V-DEF-01" in m.checks}
    assert with_def == {"chargeback_rate_value"}
    assert pack.metrics["chargeback_rate_value"].precomputed_columns[0].column == "cb_rate"


def test_content_hash_is_stable(pack) -> None:
    assert load_pack("payments").content_sha256 == pack.content_sha256


def test_missing_pack_fails_loudly(tmp_path) -> None:
    with pytest.raises(PackError):
        load_pack("nope", packs_dir=tmp_path)


def test_checker_only_sources_are_never_read_by_governed_templates(pack) -> None:
    from datetime import date

    from copilot.packs.compiler import MetricRequest, compile_metric

    checker_only = {
        n for n, s in pack.pack.sources.items() if s.checker_only or s.layer == "control"
    }
    assert "meta_load_log" in checker_only
    for m in pack.metrics.values():
        req = MetricRequest(
            m.id, date(2025, 7, 1), date(2025, 7, 31), date(2025, 10, 15), "month", ("period",)
        )
        for path in ("preferred", "independent"):
            q = compile_metric(pack, req, path)
            assert q.sources, (m.id, path)
            assert not set(q.sources) & checker_only, (m.id, path, q.sources)


def test_a_metric_reading_a_checker_only_source_is_rejected(pack) -> None:
    from copilot.packs.loader import validate_pack

    bad = pack.model_copy(deep=True)
    bad.metrics["settled_volume_eur"].independent_sources.append("meta_load_log")
    with pytest.raises(PackError, match="checker-only"):
        validate_pack(bad)
