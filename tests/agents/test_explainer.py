"""Failure explainer: code-derived figures, same-slice related findings, and output checks."""

from __future__ import annotations

import pytest

from copilot.agents.explainer import (
    Explanation,
    check_explanation,
    derived_facts,
    explain_failure,
    explainer_input,
    fmt_ratio,
    reconciliation_facts,
    related_findings,
)
from copilot.config import Settings
from copilot.contracts.validation import CheckResult, ValidationReport
from copilot.llm.fake import ScriptedTransport
from copilot.llm.gateway import CallContext, Gateway, LlmEscalation, RunBudget

AUG = "2025-08-01"


def _line(line, measure, result, ledger, period=AUG):
    return {
        "line": line,
        "measure": measure,
        "period": period,
        "result": result,
        "ledger": ledger,
        "rel_diff": abs(result - ledger) / ledger if ledger else 0.0,
    }


def _check(cid, status, slices, measured=None, threshold=None, metric="m"):
    return CheckResult.build(
        check_id=cid,
        check_name=cid,
        metric_id=metric,
        status=status,
        principle_refs=[3],
        measured=measured or {},
        threshold=threshold or {},
        affected_slices=slices,
    )


def _by_line(facts):
    return {(f["line"], f["measure"]): f for f in facts}


# ---- (b) ratio, negative gaps, share of gap -------------------------------------------------
def test_ratio_is_na_when_expected_is_zero():
    assert fmt_ratio(5.0, 0.0) == "n/a"
    facts = _by_line(
        reconciliation_facts([_line("ES", "v", 50.0, 0.0), _line("total", "v", 150, 100)])
    )
    assert facts[("ES", "v")]["ratio"] == "n/a"
    assert facts[("ES", "v")]["share_of_total_gap"] == "100.0%"


def test_exact_double_is_2_00x_with_full_share():
    lines = [_line("total", "v", 6827787.46, 5930939.33), _line("ES", "v", 1793696.26, 896848.13)]
    es = _by_line(reconciliation_facts(lines))[("ES", "v")]
    assert es["ratio"] == "2.00×" and es["share_of_total_gap"] == "100.0%"
    assert es["observed"] == "1,793,696.26" and es["difference"] == "+896,848.13"


def test_two_contributing_slices_split_the_gap():
    lines = [
        _line("total", "v", 1300.0, 1000.0),  # gap +300
        _line("ES", "v", 400.0, 200.0),  # +200 -> 66.7%
        _line("FR", "v", 200.0, 100.0),  # +100 -> 33.3%
    ]
    f = _by_line(reconciliation_facts(lines))
    assert f[("ES", "v")]["share_of_total_gap"] == "66.7%"
    assert f[("FR", "v")]["share_of_total_gap"] == "33.3%"
    assert f[("ES", "v")]["ratio"] == "2.00×" and f[("FR", "v")]["ratio"] == "2.00×"


def test_negative_gaps_and_offsetting_slices():
    lines = [
        _line("total", "v", 800.0, 1000.0),  # gap -200 (under the ledger)
        _line("ES", "v", 50.0, 300.0),  # -250 -> 125.0% of the gap
        _line("FR", "v", 150.0, 100.0),  # +50 offsets -> -25.0%
    ]
    f = _by_line(reconciliation_facts(lines))
    assert f[("total", "v")]["difference"] == "-200.00" and f[("total", "v")]["ratio"] == "0.80×"
    assert (
        f[("ES", "v")]["difference"] == "-250.00"
        and f[("ES", "v")]["share_of_total_gap"] == "125.0%"
    )
    assert f[("FR", "v")]["share_of_total_gap"] == "-25.0%"


def test_share_is_na_when_the_total_reconciles():
    lines = [_line("total", "v", 1000.0, 1000.0), _line("ES", "v", 120.0, 100.0)]
    f = _by_line(reconciliation_facts(lines))
    assert ("total", "v") not in f  # a reconciled total is left out
    assert f[("ES", "v")]["share_of_total_gap"] == "n/a"


def test_shares_are_per_period_and_measure():
    lines = [
        _line("total", "v", 1100.0, 1000.0),
        _line("ES", "v", 200.0, 100.0),
        _line("total", "c", 30.0, 20.0),
        _line("ES", "c", 25.0, 20.0),
    ]
    f = _by_line(reconciliation_facts(lines))
    assert f[("ES", "v")]["share_of_total_gap"] == "100.0%"
    assert f[("ES", "c")]["share_of_total_gap"] == "50.0%"


def test_percent_forms_for_relative_differences_and_thresholds():
    c = _check(
        "V-DUAL-01", "FAIL", [], {"max_rel_diff": 0.5, "rows_compared": 16}, {"relative": 0.001}
    )
    assert derived_facts(c) == {"max_rel_diff": "50.0%", "threshold_relative": "0.1%"}


# ---- (c) related findings: same slice only ---------------------------------------------------
ES_AUG = [{"merchant_country": "ES", "period": AUG, "status": "FAIL"}]


def _report():
    rec = _check(
        "V-REC-01",
        "FAIL",
        ES_AUG,
        {
            "lines": [
                _line("total", "v", 300, 200),
                _line("ES", "v", 200, 100),
                _line("FR", "v", 100, 100),
            ]
        },
    )
    pk = _check(
        "V-PK-01",
        "FAIL",
        [{"merchant_country": "ES", "period": AUG, "source": "grp", "status": "FAIL"}],
        {"duplicate_keys": {"grp": 10, "src": 0}},
    )
    other_country = _check(
        "V-COMP-01", "FAIL", [{"merchant_country": "FR", "period": AUG, "status": "FAIL"}]
    )
    other_period = _check(
        "V-TIME-01", "WARN", [{"merchant_country": "ES", "period": "2025-07-01", "status": "WARN"}]
    )
    month_only = _check("V-MAT-01", "WARN", [{"period": AUG, "status": "WARN"}])
    passed = _check("V-SCH-01", "PASS", ES_AUG)
    other_metric = _check("V-PK-01", "FAIL", ES_AUG, metric="other")
    return ValidationReport.build(
        "r", [rec, pk, other_country, other_period, month_only, passed, other_metric]
    ), rec


def test_related_findings_are_limited_to_the_same_slice():
    report, rec = _report()
    related = related_findings(rec, report)
    assert [r["check_id"] for r in related] == ["V-PK-01"]  # source/status do not matter
    assert related[0]["slices"] == [{"merchant_country": "ES", "period": AUG}]
    assert related[0]["derived"]["duplicate_keys"] == {"grp": "10", "src": "0"}


def test_related_reconciliation_lines_are_filtered_to_the_slice():
    report, rec = _report()
    pk = next(c for c in report.checks if c.check_id == "V-PK-01" and c.metric_id == "m")
    related = {r["check_id"]: r for r in related_findings(pk, report)}
    lines = related["V-REC-01"]["derived"]["lines"]
    assert [ln["line"] for ln in lines] == ["ES"]  # not FR, not the total


GOOD_CAUSE = "likely cause, supported by V-PK-01: duplicated ES rows in grp."


def _data():
    report, rec = _report()
    return explainer_input(rec, report)


def _exp(cause=GOOD_CAUSE, summary="ES is 200.00 against 100.00 (2.00×).", impact="Overstated."):
    return Explanation(summary=summary, likely_cause=cause, impact=impact)


def test_wording_supported_by_a_related_check_passes():
    assert check_explanation(_exp(), _data()) is None
    assert (
        check_explanation(_exp("Likely cause, supported by V-PK-01: duplicates."), _data()) is None
    )
    assert check_explanation(_exp("cause not established"), _data()) is None


@pytest.mark.parametrize(
    "cause,msg",
    [
        ("Duplicated rows in grp.", "must be"),  # free-form cause
        ("likely cause, supported by V-COMP-01: FR is wrong.", "V-COMP-01"),  # other slice
        ("likely cause, supported by V-REC-01: itself.", "V-REC-01"),  # not a related finding
        ("probably duplicates (V-PK-01)", "must be"),
    ],
)
def test_unsupported_or_misworded_causes_are_rejected(cause, msg):
    problem = check_explanation(_exp(cause), _data())
    assert problem and msg in problem


def test_invented_numbers_are_rejected_in_any_field():
    problem = check_explanation(_exp(impact="Roughly 40% too high."), _data())
    assert problem and "40%" in problem


# ---- integration through the gateway ---------------------------------------------------------
def _gateway(responses):
    t = ScriptedTransport({"failure_explainer": responses})
    return Gateway(Settings(), t), t


def test_guard_failure_gets_one_retry_with_the_reason():
    report, rec = _report()
    bad = _exp(summary="ES is about 2.1× the ledger.")
    gw, t = _gateway([bad, _exp()])
    out = explain_failure(gw, rec, report, CallContext(RunBudget(10**6)))
    assert out == _exp() and len(t.requests) == 2
    feedback = t.requests[1].messages[-1][1]
    assert "did not pass a code check" in feedback and "2.1×" in feedback


def test_second_guard_failure_escalates():
    report, rec = _report()
    gw, _ = _gateway([_exp(summary="2.1×"), _exp(cause="Duplicates.")])
    with pytest.raises(LlmEscalation, match="twice"):
        explain_failure(gw, rec, report, CallContext(RunBudget(10**6)))
