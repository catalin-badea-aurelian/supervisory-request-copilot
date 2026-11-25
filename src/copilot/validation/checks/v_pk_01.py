"""V-PK-01 Key uniqueness: primary keys are unique in every source the metric reads.

"Every source" is the union of the sources read by the preferred and the independent
template (recorded by the compiler), so the parent-group product is covered for every
metric it serves. Duplicates are counted within the request period and as-of date.
"""

from __future__ import annotations

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import iso_date, normalise_country
from copilot.validation.sql import ident, period_expr, period_filter


def gather(ctx: CheckContext) -> Evidence:
    frames: dict[str, pd.DataFrame] = {}
    undeclared: list[str] = []
    for name in ctx.sources_read():
        src = ctx.pack.pack.sources[name]
        if not src.primary_key:
            undeclared.append(name)
            continue
        has_period = src.period_column is not None
        frames[name] = ctx.run_sql(
            "pk_duplicates",
            source=name,
            key_cols=[ident(c) for c in src.primary_key],
            period_expr=period_expr(src) if has_period else "",
            period_filter=period_filter(src) if has_period else "",
            country_col=ident(src.country_column) if src.country_column else "",
        )
    return Evidence(
        frames=frames, info={"undeclared": undeclared, "unknown_country": ctx.unknown_country}
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    unknown = ev.info.get("unknown_country", "XX")
    slices: list[dict[str, str]] = []
    per_source: dict[str, int] = {}
    for name, df in sorted(ev.frames.items()):
        dups = df[pd.to_numeric(df["duplicate_keys"]).fillna(0) > 0]
        per_source[name] = int(pd.to_numeric(dups["duplicate_keys"]).sum()) if len(dups) else 0
        for _, row in dups.iterrows():
            s = {"source": name, "status": "FAIL"}
            if "period" in row:
                s["period"] = iso_date(row["period"])
            if "merchant_country" in row:
                s["merchant_country"] = normalise_country(
                    pd.Series([row["merchant_country"]]), unknown
                ).iloc[0]
            slices.append(s)
    for name in ev.info.get("undeclared", []):
        slices.append({"source": name, "line": "no_primary_key_declared", "status": "WARN"})
    status = (
        "FAIL" if any(s["status"] == "FAIL" for s in slices) else ("WARN" if slices else "PASS")
    )
    return Outcome(
        status,  # type: ignore[arg-type]
        {"duplicate_keys": per_source, "sources_checked": sorted(ev.frames)},
        slices,
    )


SPEC = register(CheckSpec("V-PK-01", "Key uniqueness", (3,), "FAIL", gather, evaluate))
