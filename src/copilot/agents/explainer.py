"""Failure explainer (fast model): plain-language text for a failed check, shown at G2.

It sees only the check result (id, name, measured values, threshold, affected slices). Its
text is labelled as model-written, is stored outside the validation report (so the report
hash stays deterministic) and can never change a status.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from copilot.contracts.validation import CheckResult
from copilot.llm.gateway import CallContext, Gateway

LABEL = "Explanation written by a language model from the check output; statuses are set by code."


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str  # one or two sentences
    likely_cause: str
    impact: str


def explain_failure(gateway: Gateway, check: CheckResult, ctx: CallContext) -> Explanation:
    data = check.model_dump(
        mode="json",
        include={
            "check_id",
            "check_name",
            "metric_id",
            "status",
            "measured",
            "threshold",
            "affected_slices",
        },
    )
    out, _ = gateway.call("failure_explainer", data, Explanation, ctx)
    return out
