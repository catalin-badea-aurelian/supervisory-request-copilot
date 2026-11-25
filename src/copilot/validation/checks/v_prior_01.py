"""V-PRIOR-01 Consistency with prior submissions (WARN only).

Quarters fully inside the request period that were submitted before the as-of date are
recomputed at quarter x country on the primary path and compared with the submitted value.
Not applicable when the request filters on MCC (submissions are per country only).
"""

from __future__ import annotations

from datetime import date

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import add_months, month_end, normalise, quarter_label, rel_diff


def _quarters_inside(start: date, end: date) -> list[date]:
    q = start if (start.month - 1) % 3 == 0 else add_months(start, 3 - (start.month - 1) % 3)
    out = []
    while month_end(add_months(q, 2)) <= end:
        out.append(q)
        q = add_months(q, 3)
    return out


def gather(ctx: CheckContext) -> Evidence:
    req = ctx.req
    if "mcc_group" in req.filters:
        return Evidence(info={"applicable": False, "reason": "MCC filter"})
    quarters = _quarters_inside(req.period_start, req.period_end)
    if not quarters:
        return Evidence(info={"applicable": False, "reason": "no full quarter in the period"})
    labels = [quarter_label(q) for q in quarters]
    prior = ctx.run_sql("prior_submissions", metric_id=ctx.metric.id, periods=labels)
    if prior.empty:
        return Evidence(info={"applicable": False, "reason": "no prior submission overlaps"})
    filters = {k: v for k, v in req.filters.items() if k == "merchant_country"}
    recomputed = ctx.run_metric(
        ctx.variant(
            period_start=quarters[0],
            period_end=month_end(add_months(quarters[-1], 2)),
            grain="quarter",
            dims=("period", "merchant_country"),
            filters=filters,
        )
    )
    return Evidence(
        frames={"prior": prior, "recomputed": recomputed},
        info={
            "applicable": True,
            "value": ctx.metric.id,
            "unknown_country": ctx.unknown_country,
        },
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    if not ev.info.get("applicable"):
        return Outcome("PASS", {"applicable": False, "reason": ev.info.get("reason", "")})
    tol = thresholds.get("relative", 0.001)
    unknown = ev.info["unknown_country"]
    rec = normalise(ev.frames["recomputed"], ["period", "merchant_country"], unknown)
    rec["quarter"] = [quarter_label(date.fromisoformat(p)) for p in rec["period"]]
    prior = normalise(ev.frames["prior"], ["period", "merchant_country"], unknown)
    m = prior.merge(
        rec[["quarter", "merchant_country", ev.info["value"]]],
        left_on=["period", "merchant_country"],
        right_on=["quarter", "merchant_country"],
        how="left",
    )
    slices: list[dict[str, str]] = []
    diffs: list[dict[str, object]] = []
    for _, row in m.iterrows():
        d = rel_diff(float(row["value"]), float(row[ev.info["value"]]))
        if d > tol:
            s = {"period": row["period"], "merchant_country": row["merchant_country"]}
            diffs.append({**s, "submitted": row["value"], "recomputed": row[ev.info["value"]]})
            slices.append({**s, "status": "WARN"})
    return Outcome(
        "WARN" if slices else "PASS",
        {"compared": len(m), "differences": diffs},
        slices,
    )


SPEC = register(
    CheckSpec(
        "V-PRIOR-01",
        "Consistency with prior submissions",
        (7, 9),
        "WARN",
        gather,
        evaluate,
        max_status="WARN",
    )
)
