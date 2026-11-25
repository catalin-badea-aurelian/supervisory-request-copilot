"""V-TIME-01 Data freshness: the load log shows every source complete for every month.

Covers the sources read by both paths that the load log tracks. The ratio is
row_count / expected_row_count; over-counts are reported but not flagged (duplicates and
unknown-country rows are other checks' findings).
"""

from __future__ import annotations

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import month_starts


def gather(ctx: CheckContext) -> Evidence:
    sources = ctx.sources_read()
    months = [m.strftime("%Y-%m") for m in month_starts(ctx.req.period_start, ctx.req.period_end)]
    log = ctx.run_sql("load_log", tables=sources, months=months)
    return Evidence(frames={"log": log}, info={"sources": sources, "months": months})


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    warn, fail = thresholds.get("warn", 0.99), thresholds.get("fail", 0.95)
    log: pd.DataFrame = ev.frames["log"]
    tracked = sorted(set(log["table_name"].astype(str)))
    slices: list[dict[str, str]] = []
    ratios: dict[str, float] = {}
    missing_months: list[dict[str, str]] = []
    for table in tracked:
        rows = log[log["table_name"] == table].set_index("partition_month")
        for month in ev.info["months"]:
            if month not in rows.index:
                missing_months.append({"source": table, "period": f"{month}-01"})
                slices.append({"source": table, "period": f"{month}-01", "status": "FAIL"})
                continue
            exp = float(rows.loc[month, "expected_row_count"])
            if exp <= 0:
                continue
            ratio = float(rows.loc[month, "row_count"]) / exp
            ratios[f"{table}|{month}"] = ratio
            status = "FAIL" if ratio < fail else ("WARN" if ratio < warn else None)
            if status:
                slices.append({"source": table, "period": f"{month}-01", "status": status})
    statuses = {s["status"] for s in slices}
    status = "FAIL" if "FAIL" in statuses else ("WARN" if statuses else "PASS")
    return Outcome(
        status,  # type: ignore[arg-type]
        {
            "min_ratio": min(ratios.values(), default=1.0),
            "ratios_below_one": {k: v for k, v in ratios.items() if v < 1.0},
            "tracked": tracked,
            "not_tracked": sorted(set(ev.info["sources"]) - set(tracked)),
            "missing_months": missing_months,
        },
        slices,
    )


SPEC = register(CheckSpec("V-TIME-01", "Data freshness", (5,), "WARN/FAIL", gather, evaluate))
