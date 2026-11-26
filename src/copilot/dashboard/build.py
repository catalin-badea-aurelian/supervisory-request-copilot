"""Dashboard builder: a spec generated deterministically from the domain pack (ADR 016).

The structure (pages, tiles, metrics, dimensions, filters, thresholds) is code. A language
model may write only titles and captions, through the gateway (replay by default); the number
guard rejects any number that is not in its input, and without a recording the template
captions below are used and the spec says so (`caption_source`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from copilot.dashboard.spec import (
    DashboardSpec,
    Page,
    Thresholds,
    Tile,
    check_spec,
)
from copilot.guards.numbers import guard_message, invented_numbers
from copilot.llm.gateway import CallContext, Gateway
from copilot.packs.models import DomainPack

EWI_KPIS = ("chargeback_rate_value", "fraud_loss_rate_bps", "refund_rate_value")
NON_RAG_COUNTRY = "XX"


def _thr(pack: DomainPack, metric_id: str) -> Thresholds | None:
    e = pack.metrics[metric_id].ewi
    return Thresholds(direction=e.direction, amber=e.amber, red=e.red) if e else None


def _metric_tile(
    pack: DomainPack, tid: str, kind: str, metric_id: str, title: str, scope: str, **extra: Any
) -> Tile:
    non_additive = metric_id == "top10_merchant_concentration"
    return Tile(
        id=tid,
        kind=kind,  # type: ignore[arg-type]
        data="metric_value",
        title=title,
        source="mart_ewi_monthly" if non_additive else "mart_ewi_components",
        metric_id=metric_id,
        dimensions=extra.pop("dimensions", ["period"]),
        thresholds=_thr(pack, metric_id),
        params={"scope": scope, **extra},
    )


def _dq(tid: str, kind: str, title: str, checks: list[str], statuses: list[str], **p: Any) -> Tile:
    return Tile(
        id=tid,
        kind=kind,  # type: ignore[arg-type]
        data="dq_cells",
        title=title,
        source="mart_dq_status",
        params={"check_ids": checks, "statuses": statuses, **p},
    )


def build_spec(
    pack: DomainPack, dashboard_id: str, scan_run_id: str | None, as_of: str | None = None
) -> DashboardSpec:
    name = {m: pack.metrics[m].name for m in pack.metrics}
    risk = [
        _metric_tile(
            pack,
            "kpi_chargeback_rate",
            "kpi",
            "chargeback_rate_value",
            "Chargeback rate (value), latest month",
            "latest_all",
        ),
        _metric_tile(
            pack,
            "kpi_fraud_loss",
            "kpi",
            "fraud_loss_rate_bps",
            "Fraud loss rate, latest month",
            "latest_all",
        ),
        _metric_tile(
            pack,
            "kpi_refund_rate",
            "kpi",
            "refund_rate_value",
            "Refund rate, latest month",
            "latest_all",
        ),
        _metric_tile(
            pack,
            "bar_top10",
            "bar",
            "top10_merchant_concentration",
            "Top-10 merchant concentration by country, latest month",
            "latest_by_country",
            dimensions=["period", "merchant_country"],
            exclude_country=NON_RAG_COUNTRY,
        ),
        _metric_tile(
            pack,
            "heat_chargeback_rate",
            "heatmap",
            "chargeback_rate_value",
            "Chargeback rate: country × month",
            "grid",
            dimensions=["period", "merchant_country"],
            exclude_country=NON_RAG_COUNTRY,
        ),
        _metric_tile(
            pack,
            "line_chargeback_trend",
            "line",
            "chargeback_rate_value",
            "Chargeback rate by month with thresholds",
            "trend_all",
        ),
        _metric_tile(
            pack,
            "line_settled_volume",
            "line",
            "settled_volume_eur",
            "Settled volume by month and country",
            "grid",
            dimensions=["period", "merchant_country"],
            exclude_country=NON_RAG_COUNTRY,
        ),
        Tile(
            id="table_breaches",
            kind="table",
            data="breaches",
            title="Threshold breaches",
            source="mart_ewi_monthly",
            params={"exclude_country": NON_RAG_COUNTRY},
        ),
    ]
    data = [
        _dq("kpi_rec_breaks", "kpi", "Open reconciliation breaks", ["V-REC-01"], ["FAIL"]),
        Tile(
            id="bar_xx_volume",
            kind="bar",
            data="xx_volume",
            title="Unallocated volume (XX)",
            source="mart_ewi_components",
            metric_id="settled_volume_eur",
            dimensions=["period", "merchant_country"],
            thresholds=_thr(pack, "settled_volume_eur"),
            params={"country": NON_RAG_COUNTRY},
        ),
        _dq(
            "kpi_completeness",
            "kpi",
            "Completeness issues",
            ["V-COMP-01", "V-COMP-02"],
            ["WARN", "FAIL"],
        ),
        Tile(
            id="kpi_partitions",
            kind="kpi",
            data="timeliness",
            title="Incomplete or late partitions",
            source="mart_timeliness",
        ),
        _dq("kpi_definition", "kpi", "Definition conformity failures", ["V-DEF-01"], ["FAIL"]),
        Tile(
            id="kpi_lineage",
            kind="kpi",
            data="lineage",
            title="Lineage coverage",
            source="mart_metric_catalog",
        ),
        _dq(
            "matrix_checks",
            "matrix",
            "Check status matrix",
            [],
            ["PASS", "WARN", "FAIL"],
            group="check_month",
        ),
        Tile(
            id="bar_principles",
            kind="bar",
            data="principles",
            title="Pass rate by BCBS 239 principle",
            source="mart_bcbs239_scorecard",
        ),
        _dq("table_findings", "table", "Finding details", [], ["FAIL"], group="findings"),
    ]
    ops = [
        Tile(
            id=f"kpi_{m}",
            kind="kpi",
            data="requests",
            title=t,
            source="mart_requests",
            params={"measure": m},
        )
        for m, t in (
            ("count", "Requests"),
            ("on_time_rate", "Delivered on time"),
            ("median_duration_hours", "Median time to delivery (hours)"),
            ("total_g2_escalations", "G2 escalations"),
            ("total_accepted_findings", "Accepted findings"),
            ("avg_cost_eur", "Average estimated cost per run (EUR)"),
        )
    ] + [
        Tile(
            id="table_requests",
            kind="table",
            data="requests",
            title="Requests",
            source="mart_requests",
            params={"measure": "table"},
        ),
        Tile(
            id="bar_gates",
            kind="bar",
            data="gates",
            title="Gate decisions",
            source="mart_gate_decisions",
        ),
    ]
    spec = DashboardSpec(
        id=dashboard_id,
        title="Supervisory oversight dashboard",
        pack_id=pack.pack.id,
        pack_version=pack.pack.version,
        as_of=as_of or pack.pack.default_as_of.isoformat(),
        scan_run_id=scan_run_id,
        pages=[
            Page(id="risk_overview", title="Risk overview", tiles=risk),
            Page(id="data_risk", title="Data risk (BCBS 239)", tiles=data),
            Page(id="request_operations", title="Request operations", tiles=ops),
        ],
    )
    _ = name
    spec = apply_template_captions(spec, pack)
    check_spec(spec, pack)
    return spec


# ---- captions: templates by default, language model optional ----------------------------------
def _template_caption(t: Tile, pack: DomainPack) -> str | None:
    if t.metric_id and t.data == "metric_value":
        m = pack.metrics[t.metric_id]
        base = f"{m.name}. Figures computed by the oversight team from raw sources."
        return base + (
            " Threshold lines show the pack's amber and red levels." if t.thresholds else ""
        )
    return {
        "breaches": "Country-month values at or above the amber threshold; XX is excluded.",
        "xx_volume": "Volume with no country. Shown here, left out of the country RAG.",
        "dq_cells": "Counts of metric × month × country cells from the latest scan.",
        "principles": "Share of scan cells that passed, by BCBS 239 principle.",
        "timeliness": "Partitions loaded below the completeness thresholds.",
        "lineage": "Metrics with an independent path and documented lineage.",
        "requests": "From the run telemetry in spb_ops.",
        "gates": "Human gate outcomes recorded in the audit logs.",
    }.get(t.data)


def apply_template_captions(spec: DashboardSpec, pack: DomainPack) -> DashboardSpec:
    pages = [
        p.model_copy(
            update={
                "tiles": [
                    t.model_copy(update={"caption": _template_caption(t, pack)}) for t in p.tiles
                ]
            }
        )
        for p in spec.pages
    ]
    return spec.model_copy(update={"pages": pages, "caption_source": "template"})


class TileText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tile_id: str
    title: str
    caption: str


class DashboardTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiles: list[TileText]


def caption_input(spec: DashboardSpec, pack: DomainPack) -> dict[str, Any]:
    out = []
    for t in spec.tiles():
        m = pack.metrics.get(t.metric_id) if t.metric_id else None
        out.append(
            {
                "tile_id": t.id,
                "kind": t.kind,
                "title": t.title,
                "metric": {"id": m.id, "name": m.name, "definition": m.definition} if m else None,
                "draft_caption": t.caption,
            }
        )
    return {"dashboard": spec.title, "tiles": out}


def check_texts(out: DashboardTexts, data: dict[str, Any]) -> str | None:
    want = [t["tile_id"] for t in data["tiles"]]
    got = [t.tile_id for t in out.tiles]
    if sorted(got) != sorted(want):
        return f"tile ids must be exactly {want}"
    bad = invented_numbers(out.model_dump(), data)
    return guard_message(bad) if bad else None


def apply_llm_captions(
    spec: DashboardSpec, pack: DomainPack, gateway: Gateway, ctx: CallContext
) -> DashboardSpec:
    """Titles and captions only; structure, metrics and thresholds are never touched."""
    from copilot.llm.prompts import load_prompt

    data = caption_input(spec, pack)
    out, _ = gateway.call(
        "dashboard_captions", data, DashboardTexts, ctx, validate=lambda o: check_texts(o, data)
    )
    by_id = {t.tile_id: t for t in out.tiles}
    pages = [
        p.model_copy(
            update={
                "tiles": [
                    t.model_copy(
                        update={"title": by_id[t.id].title, "caption": by_id[t.id].caption}
                    )
                    for t in p.tiles
                ]
            }
        )
        for p in spec.pages
    ]
    prompt = load_prompt("dashboard_captions")
    new = spec.model_copy(update={"pages": pages, "caption_source": f"llm:{prompt.ref}"})
    check_spec(new, pack)
    return new
