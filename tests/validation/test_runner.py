"""Runner: registry and pack agree, thresholds come from the pack, reports are deterministic."""

from datetime import date

import pytest

from copilot.packs.compiler import MetricRequest, compile_metric
from copilot.validation.checks import REGISTRY
from copilot.validation.runner import ValidationError, run_checks, thresholds_for
from copilot.validation.target import ValidationTarget, prepare_target

AS_OF = date(2025, 10, 15)
REQ = MetricRequest(
    "chargeback_rate_value",
    date(2025, 7, 1),
    date(2025, 8, 31),
    AS_OF,
    "month",
    ("period", "merchant_country"),
)


def test_every_check_in_the_pack_is_registered(pack) -> None:
    used = {c for m in pack.metrics.values() for c in m.checks}
    assert used <= set(REGISTRY)
    assert set(REGISTRY) == used  # no orphan checks either


def test_catalogue_metadata() -> None:
    for spec in REGISTRY.values():
        assert spec.principles and all(1 <= p <= 14 for p in spec.principles)
        assert spec.default_severity in {"FAIL", "WARN", "WARN/FAIL"}
    warn_only = {cid for cid, s in REGISTRY.items() if s.max_status == "WARN"}
    assert warn_only == {"V-VAR-01", "V-PRIOR-01", "V-MAT-01", "V-SQL-01"}


def test_thresholds_merge_pack_defaults_and_metric_tolerances(pack) -> None:
    assert thresholds_for(pack, "chargeback_rate_value", "V-TIME-01") == {
        "warn": 0.99,
        "fail": 0.95,
    }
    assert thresholds_for(pack, "chargeback_rate_value", "V-REC-01") == {"relative": 0.005}
    assert thresholds_for(pack, "chargeback_rate_value", "V-DEF-01") == {"absolute": 1e-9}


def test_compiler_records_the_sources_each_path_reads(pack) -> None:
    pref = compile_metric(pack, REQ, "preferred", "duckdb")
    indep = compile_metric(pack, REQ, "independent", "duckdb")
    assert pref.sources == ("grp_merchant_monthly",)
    assert indep.sources == ("src_chargebacks", "src_merchants", "src_transactions")


def test_report_is_deterministic_and_covers_the_check_set(small_clean, pack) -> None:
    t = prepare_target(pack, REQ, small_clean.backend, request_id="REQ-T")
    r1 = run_checks([t], pack, small_clean.backend)
    r2 = run_checks(
        [prepare_target(pack, REQ, small_clean.backend, request_id="REQ-T")],
        pack,
        small_clean.backend,
    )
    assert r1.report_sha256 == r2.report_sha256
    assert r1.verify_hash()
    assert [c.check_id for c in r1.checks] == sorted(pack.metrics[REQ.metric_id].checks)
    assert r1.request_id == "REQ-T"


def test_validation_of_the_independent_path_compares_against_the_preferred_one(
    small_dataset, pack
) -> None:
    """After a G2 'deliver from independent path', the delivered result is validated."""
    d1 = small_dataset("D1")
    t = prepare_target(pack, REQ, d1.backend, path="independent")
    rep = run_checks([t], pack, d1.backend)
    by_id = {c.check_id: c for c in rep.checks}
    assert by_id["V-DUAL-01"].measured["paths"] == ["independent", "preferred"]
    assert by_id["V-DUAL-01"].status == "FAIL"  # the group product still disagrees
    assert by_id["V-REC-01"].status == "PASS"  # the delivered raw-path figures reconcile


def test_non_template_result_raises_the_policy_warning(small_clean, pack) -> None:
    t = prepare_target(pack, REQ, small_clean.backend)
    flagged = ValidationTarget(t.request_id, t.request, t.primary, t.primary_result, True)
    rep = run_checks([flagged], pack, small_clean.backend)
    assert {c.check_id: c.status for c in rep.checks}["V-SQL-01"] == "WARN"


def test_empty_target_list_is_rejected(pack, small_clean) -> None:
    with pytest.raises(ValidationError):
        run_checks([], pack, small_clean.backend)
