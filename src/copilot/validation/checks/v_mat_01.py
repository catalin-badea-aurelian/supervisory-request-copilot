"""V-MAT-01 Maturity window (WARN only, never FAIL).

Some events arrive well after the transaction they belong to. Chargebacks, for example, are
attributed to the original transaction month but raised weeks later. A month whose end plus
the metric's `maturity_days` falls after the as-of date is not yet complete. Its figure is
expected to rise as late events arrive. That is a known property of the metric, not a data
defect, so this check can only WARN.
"""

from __future__ import annotations

from datetime import date, timedelta

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import month_end, month_starts


def gather(ctx: CheckContext) -> Evidence:
    months = month_starts(ctx.req.period_start, ctx.req.period_end)
    return Evidence(
        info={
            "months": [m.isoformat() for m in months],
            "as_of": ctx.req.as_of.isoformat(),
            "maturity_days": ctx.metric.maturity_days,
        }
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    days = ev.info.get("maturity_days")
    if not days:
        return Outcome("PASS", {"applicable": False, "reason": "no maturity window declared"})
    as_of = date.fromisoformat(ev.info["as_of"])
    immature = []
    for m in ev.info["months"]:
        matures_on = month_end(date.fromisoformat(m)) + timedelta(days=int(days))
        if matures_on > as_of:
            immature.append({"period": m, "matures_on": matures_on.isoformat()})
    slices = [{"period": x["period"], "status": "WARN"} for x in immature]
    return Outcome(
        "WARN" if immature else "PASS",
        {"maturity_days": int(days), "as_of": ev.info["as_of"], "immature": immature},
        slices,
    )


SPEC = register(
    CheckSpec(
        "V-MAT-01",
        "Maturity window",
        (5, 7),
        "WARN",
        gather,
        evaluate,
        max_status="WARN",
    )
)
