"""V-SQL-01 Query policy: the result comes from a governed template; anything else is flagged."""

from __future__ import annotations

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register


def gather(ctx: CheckContext) -> Evidence:
    return Evidence(
        info={
            "non_template": ctx.target.non_template,
            "template_id": ctx.target.primary.template_id,
        }
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    non_template = bool(ev.info.get("non_template"))
    return Outcome(
        "WARN" if non_template else "PASS",
        {"template_id": ev.info.get("template_id"), "non_template": non_template},
        [{"line": "non_template_query", "status": "WARN"}] if non_template else [],
    )


SPEC = register(
    CheckSpec("V-SQL-01", "Query policy", (1, 2), "WARN", gather, evaluate, max_status="WARN")
)
