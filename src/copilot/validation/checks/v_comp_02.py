"""V-COMP-02 Perimeter coverage: every in-scope country x period (x filtered MCC) is present.

A combination counts as covered when the result has a row for it, even if its value is zero.
"""

from __future__ import annotations

import itertools

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import normalise, period_starts


def gather(ctx: CheckContext) -> Evidence:
    req = ctx.req
    axes: dict[str, list[str]] = {}
    if "period" in req.dims:
        axes["period"] = [
            d.isoformat() for d in period_starts(req.period_start, req.period_end, req.grain)
        ]
    if "merchant_country" in req.dims:
        countries = req.filters.get("merchant_country") or tuple(
            c
            for c in ctx.pack.pack.dimensions["merchant_country"].values
            if c != ctx.unknown_country
        )
        axes["merchant_country"] = sorted(c for c in countries if c != ctx.unknown_country)
    if "mcc_group" in req.dims and "mcc_group" in req.filters:
        axes["mcc_group"] = sorted(req.filters["mcc_group"])
    return Evidence(
        frames={"result": ctx.target.primary_result},
        info={"axes": axes, "dims": list(req.dims), "unknown_country": ctx.unknown_country},
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    axes: dict[str, list[str]] = ev.info["axes"]
    df = normalise(ev.frames["result"], ev.info["dims"], ev.info["unknown_country"])
    if not axes:
        ok = len(df) > 0
        slices = [] if ok else [{"line": "total", "status": "FAIL"}]
        return Outcome("PASS" if ok else "FAIL", {"expected": 1, "missing": int(not ok)}, slices)
    names = sorted(axes)
    present = set(map(tuple, df[names].astype(str).itertuples(index=False, name=None)))
    expected = list(itertools.product(*(axes[n] for n in names)))
    missing = [combo for combo in expected if combo not in present]
    slices = [{**dict(zip(names, combo, strict=True)), "status": "FAIL"} for combo in missing]
    return Outcome(
        "FAIL" if missing else "PASS",
        {"expected": len(expected), "missing": len(missing)},
        slices,
    )


SPEC = register(CheckSpec("V-COMP-02", "Perimeter coverage", (4, 8), "FAIL", gather, evaluate))
