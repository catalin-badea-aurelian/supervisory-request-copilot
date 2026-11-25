"""V-DEF-01 Definition conformity: pre-computed metric columns equal the glossary formula.

Each published value is recomputed row by row as numerator_column / denominator_column (as
declared in the metric's `precomputed_columns`). Mismatches are reported per month x country.
"""

from __future__ import annotations

import math

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import normalise
from copilot.validation.sql import ident, literals, period_expr, period_filter


def gather(ctx: CheckContext) -> Evidence:
    frames: dict[str, pd.DataFrame] = {}
    for pc in ctx.metric.precomputed_columns:
        src = ctx.pack.pack.sources[pc.table]
        extra = ""
        countries = ctx.req.filters.get("merchant_country")
        if countries and src.country_column:
            extra = f"AND {ident(src.country_column)} IN ({literals(countries)})"
        frames[f"{pc.table}.{pc.column}"] = ctx.run_sql(
            "precomputed_rows",
            source=pc.table,
            column=ident(pc.column),
            numerator=ident(pc.numerator_column),
            denominator=ident(pc.denominator_column),
            period_expr=period_expr(src),
            period_filter=period_filter(src),
            country_col=ident(src.country_column) if src.country_column else "",
            extra_filter=extra,
        )
    return Evidence(
        frames=frames,
        info={"applicable": bool(frames), "unknown_country": ctx.unknown_country},
    )


def _mismatch(published: float, recomputed: float, tol: float) -> bool:
    p_nan, r_nan = math.isnan(published), math.isnan(recomputed)
    if p_nan or r_nan:
        return p_nan != r_nan
    return abs(published - recomputed) > tol


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    if not ev.info.get("applicable"):
        return Outcome("PASS", {"applicable": False, "reason": "no pre-computed columns"})
    tol = thresholds.get("absolute", 1e-9)
    slices: list[dict[str, str]] = []
    measured: dict[str, dict[str, float | int]] = {}
    for name, raw in sorted(ev.frames.items()):
        df = normalise(raw, ["period", "merchant_country"], ev.info["unknown_country"])
        den = df["denominator"].where(df["denominator"] != 0)
        recomputed = df["numerator"] / den
        bad = [
            _mismatch(p, r, tol)
            for p, r in zip(df["published"], recomputed.fillna(float("nan")), strict=True)
        ]
        df = df.assign(recomputed=recomputed, bad=bad)
        diff = (df["published"] - df["recomputed"]).abs()
        measured[name] = {
            "rows_checked": len(df),
            "rows_mismatched": int(df["bad"].sum()),
            "max_abs_diff": float(diff.max()) if diff.notna().any() else 0.0,
        }
        for (period, country), _ in df[df["bad"]].groupby(["period", "merchant_country"]):
            slices.append(
                {
                    "source": name.split(".")[0],
                    "period": period,
                    "merchant_country": country,
                    "status": "FAIL",
                }
            )
    return Outcome("FAIL" if slices else "PASS", {"columns": measured}, slices)


SPEC = register(CheckSpec("V-DEF-01", "Definition conformity", (1, 3), "FAIL", gather, evaluate))
