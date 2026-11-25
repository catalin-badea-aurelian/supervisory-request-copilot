"""V-DUAL-01 Independent recomputation: the other source path must agree with the primary.

The other path is the independent template (or the preferred one, when the delivered result
already comes from the independent path), so the checker never compares a query with itself.
Both results get the same key normalisation before alignment: a missing or empty merchant
country becomes the pack's unknown code (XX). A missing country (D2) therefore cannot cause
a key mismatch between the paths.
"""

from __future__ import annotations

import math

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import normalise, rel_diff


def gather(ctx: CheckContext) -> Evidence:
    other = "preferred" if ctx.target.primary_path == "independent" else "independent"
    return Evidence(
        frames={
            "primary": ctx.target.primary_result,
            "other": ctx.run_metric(ctx.req, other),  # type: ignore[arg-type]
        },
        info={
            "dims": list(ctx.req.dims),
            "primary_path": ctx.target.primary_path,
            "other_path": other,
            "unknown_country": ctx.unknown_country,
        },
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    tol = thresholds.get("relative", 0.001)
    dims: list[str] = ev.info["dims"]
    unknown = ev.info["unknown_country"]
    a = normalise(ev.frames["primary"], dims, unknown)
    b = normalise(ev.frames["other"], dims, unknown)
    if dims:
        # one row per key on each side (normalisation could merge NULL and XX rows)
        a = a.groupby(dims, as_index=False).sum(min_count=1)
        b = b.groupby(dims, as_index=False).sum(min_count=1)
    values = sorted((set(a.columns) & set(b.columns)) - set(dims))
    key = dims or None
    if key:
        m = a.merge(b, on=key, how="outer", suffixes=("_p", "_o"), indicator=True)
    else:
        m = a.add_suffix("_p").join(b.add_suffix("_o"), how="outer")
        m["_merge"] = "both"
    slices: list[dict[str, str]] = []
    max_rel = 0.0
    mismatches = 0
    for _, row in m.iterrows():
        worst = 0.0
        for c in values:
            pv, ov = row[f"{c}_p"], row[f"{c}_o"]
            pv = 0.0 if pd.isna(pv) and row["_merge"] == "right_only" else float(pv)
            ov = 0.0 if pd.isna(ov) and row["_merge"] == "left_only" else float(ov)
            worst = max(worst, rel_diff(pv, ov))
        if worst > tol:
            mismatches += 1
            s = {d: str(row[d]) for d in dims} if dims else {"line": "total"}
            slices.append({**s, "status": "FAIL"})
        if not math.isinf(worst):
            max_rel = max(max_rel, worst)
    return Outcome(
        "FAIL" if slices else "PASS",
        {
            "rows_compared": len(m),
            "mismatched_rows": mismatches,
            "max_rel_diff": max_rel,
            "columns": values,
            "paths": [ev.info["primary_path"], ev.info["other_path"]],
        },
        slices,
    )


SPEC = register(
    CheckSpec("V-DUAL-01", "Independent recomputation", (3, 7), "FAIL", gather, evaluate)
)
