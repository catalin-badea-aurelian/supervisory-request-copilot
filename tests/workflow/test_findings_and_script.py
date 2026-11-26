"""Accepted findings and the scripted demo answerer."""

from __future__ import annotations

import pytest

from copilot.contracts.validation import CheckResult, ValidationReport
from copilot.workflow.findings import accept, apply_accepted, new_fails, warning_ids
from copilot.workflow.scripted import ACTOR, ANSWERS_DIR, Script, ScriptError


def _check(cid: str, status: str, slices: list[dict]) -> CheckResult:
    return CheckResult.build(
        check_id=cid,
        check_name=cid,
        metric_id="m",
        status=status,
        principle_refs=[3],
        measured={},
        threshold={},
        affected_slices=slices,
    )


ES_AUG = [{"merchant_country": "ES", "period": "2025-08", "status": "FAIL"}]


def test_accepted_fail_keeps_fail_and_carries_the_decision():
    report = ValidationReport.build("r", [_check("V-PK-01", "FAIL", ES_AUG)])
    accepted = accept(new_fails(report), "r:G2:1")
    again = apply_accepted(
        ValidationReport.build("r", [_check("V-PK-01", "FAIL", ES_AUG)]), accepted
    )
    assert again.checks[0].status == "FAIL" and again.checks[0].accepted_ref == "r:G2:1"
    assert new_fails(again) == []


def test_a_new_fail_still_escalates():
    accepted = accept([_check("V-PK-01", "FAIL", ES_AUG)], "r:G2:1")
    other_slice = [{"merchant_country": "FR", "period": "2025-08", "status": "FAIL"}]
    report = apply_accepted(
        ValidationReport.build(
            "r", [_check("V-PK-01", "FAIL", other_slice), _check("V-REC-01", "FAIL", ES_AUG)]
        ),
        accepted,
    )
    assert {c.check_id for c in new_fails(report)} == {"V-PK-01", "V-REC-01"}


def test_accepting_is_deterministic_for_the_report_hash():
    accepted = accept([_check("V-PK-01", "FAIL", ES_AUG)], "r:G2:1")
    a = apply_accepted(ValidationReport.build("r", [_check("V-PK-01", "FAIL", ES_AUG)]), accepted)
    b = apply_accepted(ValidationReport.build("r", [_check("V-PK-01", "FAIL", ES_AUG)]), accepted)
    assert a.report_sha256 == b.report_sha256 and a.verify_hash()


def test_warning_ids():
    r = ValidationReport.build("r", [_check("V-MAT-01", "WARN", []), _check("V-PK-01", "PASS", [])])
    assert warning_ids(r) == ["m:V-MAT-01"]


@pytest.mark.parametrize("name", ["s1", "s2"])
def test_demo_scripts_load_and_answer_by_topic(name):
    s = Script.load(ANSWERS_DIR / f"{name}.yaml")
    out = s({"type": "questions", "questions": [{"topic": "as_of", "text": "?"}]})
    assert out[0]["actor"] == ACTOR and "2025-10-15" in out[0]["answer"]
    g1 = s({"type": "gate", "gate": "G1", "options": ["approve", "request_changes"]})
    assert g1["decision"] == "approve" and g1["actor"] == ACTOR


def test_script_fails_loudly_on_unknown_topic_and_wrong_acknowledgements():
    s = Script.load(ANSWERS_DIR / "s2.yaml")
    s.answers.pop("other")
    with pytest.raises(ScriptError, match="no scripted answer"):
        s({"type": "questions", "questions": [{"topic": "other", "text": "?"}]})
    with pytest.raises(ScriptError, match="requires acknowledging"):
        s(
            {
                "type": "gate",
                "gate": "G3",
                "options": ["approve"],
                "required_acknowledgements": ["m:V-NEW-01"],
            }
        )
    with pytest.raises(ScriptError, match="no scripted decision"):
        s({"type": "gate", "gate": "G2", "options": ["independent_path"]})  # first one is fine
        s({"type": "gate", "gate": "G2", "options": ["independent_path"]})
