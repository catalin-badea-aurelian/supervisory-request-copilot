"""V-VAR-01 Variance vs prior period: movements beyond the threshold are flagged (WARN only).

The governed metric is recompiled with the period extended one grain step back (within the
pack's data range), on the primary path, at the requested grain and dimensions (plus period).
"""

from __future__ import annotations

import math

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import add_months, normalise


def gather(ctx: CheckContext) -> Evidence:
    req = ctx.req
    step = 3 if req.grain == "quarter" else 1
    earliest = ctx.pack.pack.data_range["start"]
    start = add_months(req.period_start, -step)
    start = start if start >= earliest else req.period_start
    dims = req.dims if "period" in req.dims else ("period", *req.dims)
    result = ctx.run_metric(ctx.variant(period_start=start, dims=dims))
    return Evidence(
        frames={"result": result},
        info={
            "dims": list(dims),
            "value": ctx.metric.id,
            "first_period": req.period_start.isoformat(),
            "unknown_country": ctx.unknown_country,
        },
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    thr = thresholds.get("relative", 0.25)
    dims: list[str] = ev.info["dims"]
    keys = [d for d in dims if d != "period"]
    col = ev.info["value"]
    df = normalise(ev.frames["result"], dims, ev.info["unknown_country"])
    flagged: list[dict[str, object]] = []
    slices: list[dict[str, str]] = []
    groups = df.groupby(keys, sort=True) if keys else [((), df)]
    for key, g in groups:
        key = key if isinstance(key, tuple) else (key,)
        g = g.sort_values("period")
        prev_val = None
        for period, val in zip(g["period"], g[col], strict=True):
            if (
                prev_val is not None
                and period >= ev.info["first_period"]
                and not math.isnan(prev_val)
                and not math.isnan(val)
                and prev_val != 0
            ):
                change = (val - prev_val) / abs(prev_val)
                if abs(change) > thr:
                    s = {**dict(zip(keys, key, strict=True)), "period": period}
                    flagged.append({**s, "previous": prev_val, "current": val, "change": change})
                    slices.append({**s, "status": "WARN"})
            prev_val = val
    return Outcome(
        "WARN" if slices else "PASS",
        {
            "flagged": flagged,
            "max_abs_change": max((abs(float(f["change"])) for f in flagged), default=0.0),  # type: ignore[arg-type]
        },
        slices,
    )


SPEC = register(
    CheckSpec(
        "V-VAR-01",
        "Variance vs prior period",
        (7,),
        "WARN",
        gather,
        evaluate,
        max_status="WARN",
    )
)
