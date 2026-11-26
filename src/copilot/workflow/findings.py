"""Accepted findings (G2) and the G3 acknowledgement list.

A FAIL decided at G2 is identified by (metric, check, affected slices). On a later validation
of the same run, a FAIL with the same key keeps status FAIL and gets `accepted_ref` (the G2
decision id): it is carried as a documented limitation and does not escalate again. Any other
FAIL escalates. The same function is used by the workflow and by `copilot rerun`, so the
report hash is reproducible.
"""

from __future__ import annotations

import json

from copilot.contracts.gates import AcceptedFinding
from copilot.contracts.validation import CheckResult, ValidationReport


def finding_key(check: CheckResult) -> str:
    slices = json.dumps(check.affected_slices, sort_keys=True, separators=(",", ":"))
    return f"{check.metric_id}|{check.check_id}|{slices}"


def apply_accepted(report: ValidationReport, accepted: list[AcceptedFinding]) -> ValidationReport:
    by_key = {a.key: a.decision_id for a in accepted}
    checks = [
        c.model_copy(update={"accepted_ref": by_key[finding_key(c)]})
        if c.status == "FAIL" and finding_key(c) in by_key
        else c
        for c in report.checks
    ]
    return ValidationReport.build(report.request_id, checks)


def new_fails(report: ValidationReport) -> list[CheckResult]:
    return [c for c in report.checks if c.status == "FAIL" and c.accepted_ref is None]


def accept(fails: list[CheckResult], decision_id: str) -> list[AcceptedFinding]:
    return [
        AcceptedFinding(
            key=finding_key(c), metric_id=c.metric_id, check_id=c.check_id, decision_id=decision_id
        )
        for c in fails
    ]


def warning_ids(report: ValidationReport) -> list[str]:
    """What G3 must acknowledge: every WARN, as metric:check."""
    return sorted({f"{c.metric_id}:{c.check_id}" for c in report.checks if c.status == "WARN"})
