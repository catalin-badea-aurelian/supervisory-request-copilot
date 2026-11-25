"""V-REC-01 Reconciliation to ledger, per month x country, with an explicit unallocated line.

The governed metric is recompiled at month x merchant_country on the primary path with no
filters, so volume whose merchant country is unknown stays visible as the pack's unknown
code (XX). The ledger books by true country and has no XX line. For each month and measure:

- total line: all countries plus XX against the ledger total
- unallocated line: the XX amount, reported explicitly
- country lines for the countries in scope

Status per month and measure:

- FAIL if an in-scope country is above the ledger beyond tolerance (XX cannot explain an
  excess), or falls short and the shortfall is not covered by the unallocated amount with a
  reconciling total
- WARN if in-scope countries fall short, the total reconciles, and the shortfalls are
  covered by the unallocated amount (allocation differences, nothing lost)
- WARN if only out-of-scope countries break (the total does not reconcile)
- PASS otherwise
"""

from __future__ import annotations

import pandas as pd

from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register
from copilot.validation.frames import normalise

TOTAL, UNALLOCATED = "total", "unallocated"


def gather(ctx: CheckContext) -> Evidence:
    rec = ctx.metric.reconciles_to
    if rec is None:
        return Evidence(info={"applicable": False})
    req = ctx.variant(grain="month", dims=("period", "merchant_country"), filters={})
    result = ctx.run_metric(req)
    ledger = ctx.run_sql("ledger_totals", ledger_table=rec.table, measures=rec.measures)
    countries = [
        c for c in ctx.pack.pack.dimensions["merchant_country"].values if c != ctx.unknown_country
    ]
    scope = sorted(ctx.req.filters.get("merchant_country") or countries)
    return Evidence(
        frames={"result": result, "ledger": ledger},
        info={
            "applicable": True,
            "measures": list(rec.measures),
            "scope": [c for c in scope if c != ctx.unknown_country],
            "unknown_country": ctx.unknown_country,
        },
    )


def _amount(series: pd.Series, key: str) -> float:
    return float(series.get(key, 0.0))


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    if not ev.info.get("applicable"):
        return Outcome("PASS", {"applicable": False, "reason": "metric does not reconcile"})
    tol = thresholds.get("relative", 0.005)
    unknown: str = ev.info["unknown_country"]
    scope: list[str] = ev.info["scope"]
    res = normalise(ev.frames["result"], ["period", "merchant_country"], unknown)
    led = normalise(ev.frames["ledger"], ["period", "merchant_country", "measure"], unknown)
    lines: list[dict[str, object]] = []
    slices: list[dict[str, str]] = []
    periods = sorted(set(res["period"]) | set(led["period"]))
    for measure in ev.info["measures"]:
        for period in periods:
            r = res[res["period"] == period].set_index("merchant_country")[measure].fillna(0.0)
            lm = led[(led["period"] == period) & (led["measure"] == measure)]
            ledger = lm.set_index("merchant_country")["amount_eur"].fillna(0.0)
            unalloc = _amount(r, unknown)
            total_r, total_l = float(r.sum()), float(ledger.sum())
            total_ok = abs(total_r - total_l) <= tol * abs(total_l) + 1e-9
            base = {"period": period, "measure": measure}
            lines.append({**base, "line": TOTAL, "result": total_r, "ledger": total_l})
            if unalloc:
                lines.append({**base, "line": UNALLOCATED, "result": unalloc, "ledger": 0.0})
            over, short = [], []
            for c in scope:
                rv, lv = _amount(r, c), _amount(ledger, c)
                if rv - lv > tol * abs(lv) + 1e-9:
                    over.append(c)
                elif lv - rv > tol * abs(lv) + 1e-9:
                    short.append(c)
                if c in over or c in short:
                    lines.append({**base, "line": c, "result": rv, "ledger": lv})
            shortfall = sum(_amount(ledger, c) - _amount(r, c) for c in short)
            explained = total_ok and shortfall <= unalloc + tol * abs(total_l) + 1e-9
            for c in over:
                slices.append({"period": period, "merchant_country": c, "status": "FAIL"})
            for c in short:
                status = "WARN" if explained else "FAIL"
                slices.append({"period": period, "merchant_country": c, "status": status})
            if short and explained:
                slices.append({"period": period, "line": UNALLOCATED, "status": "WARN"})
            if not over and not short and not total_ok:
                slices.append({"period": period, "line": TOTAL, "status": "WARN"})
    statuses = {s["status"] for s in slices}
    status = "FAIL" if "FAIL" in statuses else ("WARN" if statuses else "PASS")
    for line in lines:
        lv, rv = float(line["ledger"]), float(line["result"])  # type: ignore[arg-type]
        line["rel_diff"] = (rv - lv) / lv if lv else None
    return Outcome(status, {"lines": lines, "scope": scope}, slices)  # type: ignore[arg-type]


SPEC = register(CheckSpec("V-REC-01", "Reconciliation to ledger", (3, 7), "FAIL", gather, evaluate))
