"""Dashboard spec: the contract between the builder, the renderer and the reconciliation.

A spec names pages, tiles, metric ids, dimensions, filters and thresholds. Validation is code:
only glossary metrics, only the metric's allowed dimensions, only the pack's thresholds and
only known marts. The spec carries no numbers; figures come from the marts at render time.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from copilot.marts.views import VIEWS
from copilot.packs.models import DomainPack

Kind = Literal["kpi", "heatmap", "line", "bar", "table", "matrix"]
Data = Literal[
    "metric_value",
    "breaches",
    "xx_volume",
    "dq_cells",
    "principles",
    "timeliness",
    "lineage",
    "requests",
    "gates",
]
PAGE_IDS = ("risk_overview", "data_risk", "request_operations")
DISCLAIMER = "Synthetic data — fictional entity (Synthetic Payments Bank). Demonstration only."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Thresholds(_Strict):
    direction: Literal["higher_is_worse", "lower_is_worse"]
    amber: float
    red: float


class Tile(_Strict):
    id: str
    kind: Kind
    data: Data
    title: str
    caption: str | None = None
    source: str  # the mart the tile reads
    metric_id: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    thresholds: Thresholds | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Page(_Strict):
    id: str
    title: str
    tiles: list[Tile]


class DashboardSpec(_Strict):
    id: str
    title: str
    pack_id: str
    pack_version: str
    as_of: str
    scan_run_id: str | None
    disclaimer: str = DISCLAIMER
    caption_source: str = "template"  # "template" or "llm:<prompt id>@<version>"
    pages: list[Page]

    def tiles(self) -> list[Tile]:
        return [t for p in self.pages for t in p.tiles]


class SpecError(ValueError):
    pass


def spec_hash(spec: DashboardSpec) -> str:
    body = json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def dump_yaml(spec: DashboardSpec) -> str:
    header = f"# GENERATED FROM domain pack {spec.pack_id}@{spec.pack_version} — DO NOT EDIT\n"
    body = yaml.safe_dump(
        spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    return header + body


def load_yaml(text: str) -> DashboardSpec:
    return DashboardSpec(**yaml.safe_load(text))


def validate_spec(spec: DashboardSpec, pack: DomainPack) -> list[str]:
    errors: list[str] = []
    if spec.pack_id != pack.pack.id or spec.pack_version != pack.pack.version:
        errors.append(f"spec is for pack {spec.pack_id}@{spec.pack_version}")
    if tuple(p.id for p in spec.pages) != PAGE_IDS:
        errors.append(f"pages must be {list(PAGE_IDS)}")
    ids = [t.id for t in spec.tiles()]
    if len(ids) != len(set(ids)):
        errors.append("tile ids are not unique")
    check_ids = {c for m in pack.metrics.values() for c in m.checks}
    for t in spec.tiles():
        where = f"tile {t.id}"
        if t.source not in VIEWS:
            errors.append(f"{where}: unknown mart {t.source!r}")
        if t.metric_id is not None:
            m = pack.metrics.get(t.metric_id)
            if m is None or m.status != "approved":
                errors.append(f"{where}: {t.metric_id!r} is not an approved glossary metric")
                continue
            bad = [d for d in t.dimensions if d not in m.allowed_dimensions]
            if bad:
                errors.append(f"{where}: dimensions {bad} are not allowed for {t.metric_id}")
            want = None
            if m.ewi is not None:
                want = Thresholds(direction=m.ewi.direction, amber=m.ewi.amber, red=m.ewi.red)
            if t.thresholds != want:
                errors.append(f"{where}: thresholds differ from the pack's for {t.metric_id}")
        elif t.thresholds is not None:
            errors.append(f"{where}: thresholds without a metric")
        if t.data in ("metric_value", "xx_volume") and t.metric_id is None:
            errors.append(f"{where}: data {t.data} needs a metric_id")
        unknown = [c for c in t.params.get("check_ids", []) if c not in check_ids]
        if unknown:
            errors.append(f"{where}: unknown check ids {unknown}")
    return errors


def check_spec(spec: DashboardSpec, pack: DomainPack) -> None:
    errors = validate_spec(spec, pack)
    if errors:
        raise SpecError("; ".join(errors))
