"""V-SCH-01 Schema and grain: result columns, types and grain match the request."""

from __future__ import annotations

import math

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import period_starts, to_float


def expected_columns(metric_id: str, numerator: str | None, denominator: str | None) -> list[str]:
    cols = [c for c in (numerator, denominator) if c]
    return [*dict.fromkeys([*cols, metric_id])]


def gather(ctx: CheckContext) -> Evidence:
    req, m = ctx.req, ctx.metric
    cardinality: dict[str, int] = {}
    for d in req.dims:
        if d == "period":
            cardinality[d] = len(period_starts(req.period_start, req.period_end, req.grain))
        elif d in req.filters:
            cardinality[d] = len(req.filters[d])
        else:
            cardinality[d] = len(ctx.pack.pack.dimensions[d].values)
    return Evidence(
        frames={"result": ctx.target.primary_result},
        info={
            "dims": list(req.dims),
            "value_columns": expected_columns(m.id, m.numerator, m.denominator),
            "cardinality": cardinality,
        },
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    df: pd.DataFrame = ev.frames["result"]
    dims: list[str] = ev.info["dims"]
    values: list[str] = ev.info["value_columns"]
    expected = [*dims, *values]
    missing = [c for c in expected if c not in df.columns]
    unexpected = [c for c in df.columns if c not in expected]
    problems: list[dict[str, str]] = []
    if missing:
        problems.append({"line": "missing_columns", "detail": ",".join(missing)})
    if unexpected:
        problems.append({"line": "unexpected_columns", "detail": ",".join(unexpected)})
    non_numeric = []
    for c in values:
        if c in df.columns:
            coerced = to_float(df[c])
            if (coerced.isna() & df[c].notna()).any():
                non_numeric.append(c)
    if non_numeric:
        problems.append({"line": "non_numeric_columns", "detail": ",".join(non_numeric)})
    present_dims = [d for d in dims if d in df.columns]
    duplicates = int(df.duplicated(present_dims).sum()) if present_dims else max(len(df) - 1, 0)
    if duplicates:
        problems.append({"line": "duplicate_keys", "detail": str(duplicates)})
    max_rows = math.prod(ev.info["cardinality"].values()) if dims else 1
    if len(df) > max_rows:
        problems.append({"line": "fan_out", "detail": f"{len(df)}>{max_rows}"})
    slices = [{**p, "status": "FAIL"} for p in problems]
    return Outcome(
        "FAIL" if problems else "PASS",
        {"rows": len(df), "max_rows": max_rows, "columns": list(df.columns), "problems": problems},
        slices,
    )


SPEC = register(CheckSpec("V-SCH-01", "Schema and grain", (3,), "FAIL", gather, evaluate))
