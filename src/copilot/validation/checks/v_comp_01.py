"""V-COMP-01 Dimension completeness: share of unknown or null dimension values, per month.

The governed metric is recompiled at month x the requested categorical dimensions on the
primary path, without a country filter, so the unknown country (XX) stays visible. The
share is weighted by the metric's denominator (or by the metric itself for additive ones).
"""

from __future__ import annotations

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import NULL_KEY, normalise


def gather(ctx: CheckContext) -> Evidence:
    cat = [d for d in ctx.req.dims if d != "period"]
    if not cat:
        return Evidence(info={"applicable": False})
    filters = {k: v for k, v in ctx.req.filters.items() if k != "merchant_country"}
    req = ctx.variant(grain="month", dims=("period", *cat), filters=filters)
    m = ctx.metric
    return Evidence(
        frames={"result": ctx.run_metric(req)},
        info={
            "applicable": True,
            "dims": cat,
            "weight": m.denominator or m.id,
            "unknown_country": ctx.unknown_country,
        },
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    if not ev.info.get("applicable"):
        return Outcome("PASS", {"applicable": False, "reason": "no categorical dimension"})
    dims: list[str] = ev.info["dims"]
    unknown = ev.info["unknown_country"]
    df = normalise(ev.frames["result"], ["period", *dims], unknown)
    weight = ev.info["weight"]
    warn, fail = thresholds.get("warn", 0.001), thresholds.get("fail", 0.01)
    shares: dict[str, float] = {}
    slices: list[dict[str, str]] = []
    for d in dims:
        bad = df[d] == (unknown if d == "merchant_country" else NULL_KEY)
        if d == "merchant_country":
            bad |= df[d] == NULL_KEY
        g = pd.DataFrame({"period": df["period"], "w": df[weight].fillna(0), "bad": bad})
        tot = g.groupby("period")["w"].sum()
        unk = g[g["bad"]].groupby("period")["w"].sum().reindex(tot.index, fill_value=0.0)
        for period in tot.index:
            if tot[period] <= 0:
                continue
            share = float(unk[period] / tot[period])
            if share > 0:
                shares[f"{d}|{period}"] = share
            status = "FAIL" if share >= fail else ("WARN" if share >= warn else None)
            if status:
                slices.append({"period": period, "dimension": d, "status": status})
    statuses = {s["status"] for s in slices}
    status = "FAIL" if "FAIL" in statuses else ("WARN" if statuses else "PASS")
    return Outcome(
        status,  # type: ignore[arg-type]
        {"unknown_share": shares, "max_share": max(shares.values(), default=0.0)},
        slices,
    )


SPEC = register(
    CheckSpec("V-COMP-01", "Dimension completeness", (4,), "WARN/FAIL", gather, evaluate)
)
