"""Tile figures: the numbers a tile shows, as a flat JSON-able dict.

One function per tile data kind, written against the `Data` interface, so the same code gives
the dashboard's figures (from the marts) and the reconciliation's (from the reference). The
renderer draws only these figures; nothing on the page is computed anywhere else.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from copilot.contracts.validation import worst
from copilot.dashboard.data import ALL, Data
from copilot.dashboard.spec import Tile
from copilot.ops.scan import scan_months
from copilot.packs.models import DomainPack

Figures = dict[str, Any]


def _months(pack: DomainPack) -> list[str]:
    return [m.isoformat() for m in scan_months(pack)]


def _countries(pack: DomainPack, exclude: str | None) -> list[str]:
    return [c for c in pack.pack.dimensions["merchant_country"].values if c != exclude]


def metric_value(t: Tile, d: Data, pack: DomainPack) -> Figures:
    assert t.metric_id
    cells = d.cells(t.metric_id)
    months = _months(pack)
    scope = t.params["scope"]
    if scope == "latest_all":
        picks = [(m, ALL) for m in months[-2:]]
    elif scope == "latest_by_country":
        picks = [(months[-1], c) for c in _countries(pack, t.params.get("exclude_country"))]
    elif scope == "trend_all":
        picks = [(m, ALL) for m in months]
    else:  # grid
        cs = _countries(pack, t.params.get("exclude_country"))
        picks = [(m, c) for c in cs for m in months]
    return {f"{m}|{c}": cells[(m, c)] for m, c in picks if (m, c) in cells}


def breaches(t: Tile, d: Data, pack: DomainPack) -> Figures:
    out: Figures = {}
    unknown = t.params.get("exclude_country")
    for mid, m in sorted(pack.metrics.items()):
        if m.ewi is None:
            continue
        for (month, country), (status, value) in d.rag(mid).items():
            if status in ("AMBER", "RED") and country != unknown:
                out[f"{mid}|{month}|{country}"] = {"rag": status, "value": value}
    return out


def xx_volume(t: Tile, d: Data, pack: DomainPack) -> Figures:
    assert t.metric_id
    xx = t.params["country"]
    return {m: v for (m, c), v in sorted(d.cells(t.metric_id).items()) if c == xx}


def dq_cells(t: Tile, d: Data, pack: DomainPack) -> Figures:
    df = d.dq_cells()
    ids, statuses = t.params["check_ids"], t.params["statuses"]
    group = t.params.get("group")
    if group == "check_month":
        out: Figures = {}
        for (check, month), g in df.groupby(["check_id", "period_month"]):
            out[f"{check}|{month}"] = worst(list(g["status"]))
        return out
    sel = df[df["status"].isin(statuses)]
    if ids:
        sel = sel[sel["check_id"].isin(ids)]
    if group == "findings":
        out = {}
        for (metric, check, month), g in sel.groupby(["metric_id", "check_id", "period_month"]):
            out[f"{metric}|{check}|{month}"] = int(g["merchant_country"].nunique())
        return out
    cells = sel[["metric_id", "period_month", "merchant_country"]].drop_duplicates()
    return {"count": len(cells)}


def principles(t: Tile, d: Data, pack: DomainPack) -> Figures:
    return {f"P{p}": r for p, r in d.principle_rates().items()}


def timeliness(t: Tile, d: Data, pack: DomainPack) -> Figures:
    return {f"{tbl}|{m}": s for (tbl, m), s in sorted(d.timeliness().items())}


def lineage(t: Tile, d: Data, pack: DomainPack) -> Figures:
    c = d.catalog()
    covered = c["has_independent_path"].astype(bool) & c["lineage_documented"].astype(bool)
    return {"covered": int(covered.sum()), "total": len(c)}


def requests(t: Tile, d: Data, pack: DomainPack) -> Figures:
    df = d.requests()
    measure = t.params["measure"]
    if measure == "table":
        return {
            r.request_id: {
                "status": r.status,
                "g2_escalations": int(r.g2_escalations),
                "on_time": None if pd.isna(r.on_time) else bool(r.on_time),
            }
            for r in df.itertuples()
        }
    if df.empty:
        return {"value": 0 if measure == "count" else None}
    value: Any = {
        "count": len(df),
        "on_time_rate": df["on_time"].dropna().astype(float).mean(),
        "median_duration_hours": df["duration_s"].median() / 3600,
        "total_g2_escalations": int(df["g2_escalations"].sum()),
        "total_accepted_findings": int(df["accepted_findings"].sum()),
        "avg_cost_eur": df["est_cost_eur"].mean(),
    }[measure]
    return {"value": None if pd.isna(value) else float(value) if measure != "count" else value}


def gates(t: Tile, d: Data, pack: DomainPack) -> Figures:
    df = d.gates()
    counts = df.groupby(["gate", "decision"]).size()
    return {f"{g}|{dec}": int(n) for (g, dec), n in counts.items()}


FIGURES: dict[str, Callable[[Tile, Data, DomainPack], Figures]] = {
    "metric_value": metric_value,
    "breaches": breaches,
    "xx_volume": xx_volume,
    "dq_cells": dq_cells,
    "principles": principles,
    "timeliness": timeliness,
    "lineage": lineage,
    "requests": requests,
    "gates": gates,
}


def tile_figures(t: Tile, d: Data, pack: DomainPack) -> Figures:
    return FIGURES[t.data](t, d, pack)
