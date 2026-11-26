"""Pydantic models for a domain pack (pack.yaml + metrics/*.yaml)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CHECK_ID_PATTERN = r"^V-[A-Z]+-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceDef(_Strict):
    layer: Literal["raw", "group", "control"]
    description: str = ""
    # Used by the validation engine (V-PK-01): key columns, and the columns that place a
    # duplicate in a period and a country when the table has them.
    primary_key: list[str] = Field(default_factory=list)
    period_column: str | None = None
    period_column_type: Literal["date", "timestamp"] | None = None
    country_column: str | None = None
    # Readable by the validation engine only, never by metric templates, the Analyst or the
    # MCP server (e.g. the load log). Control-layer sources are always checker-only.
    checker_only: bool = False


class DimensionDef(_Strict):
    description: str = ""
    values: list[str] = Field(default_factory=list)  # empty = free-form (not enumerated)


class PackDef(_Strict):
    id: str
    version: str
    name: str
    entity: str
    data_range: dict[str, date]
    default_as_of: date
    sources: dict[str, SourceDef]
    dimensions: dict[str, DimensionDef]
    unknown_country_code: str = "XX"
    defaults: dict[str, dict[str, float]] = Field(default_factory=dict)


class EwiDef(_Strict):
    direction: Literal["higher_is_worse", "lower_is_worse"]
    amber: float
    red: float
    owner: str
    version: str


class ChangelogEntry(_Strict):
    version: str
    date: date
    change: str
    approved_by: str


class ReconcilesTo(_Strict):
    table: str
    measures: list[str]


class PrecomputedColumn(_Strict):
    """A metric column published pre-computed by a data product (checked by V-DEF-01).

    The glossary recomputation is numerator_column / denominator_column on the same row.
    """

    table: str
    column: str
    numerator_column: str
    denominator_column: str


class MetricDef(_Strict):
    id: str
    version: str
    status: Literal["draft", "approved", "retired"]
    name: str
    owner: str
    definition: str
    unit: str
    numerator: str | None = None
    denominator: str | None = None
    attribution: str
    allowed_dimensions: list[str]
    preferred_source: str
    independent_sources: list[str]
    sql_template: str
    independent_sql_template: str
    reconciles_to: ReconcilesTo | None = None
    precomputed_columns: list[PrecomputedColumn] = Field(default_factory=list)
    checks: list[str]
    tolerances: dict[str, dict[str, float]] = Field(default_factory=dict)
    bcbs239_principles: list[int] = Field(default_factory=list)
    ewi: EwiDef | None = None
    # Days after a period end before the metric's events are complete (V-MAT-01).
    maturity_days: int | None = None
    limitations: list[str] = Field(default_factory=list)
    changelog: list[ChangelogEntry] = Field(default_factory=list)

    @field_validator("checks")
    @classmethod
    def _check_ids(cls, v: list[str]) -> list[str]:
        import re

        for cid in v:
            if not re.match(CHECK_ID_PATTERN, cid):
                raise ValueError(f"malformed check id: {cid}")
        return v


class DomainPack(BaseModel):
    """A loaded pack: definition, metrics, and a content hash for the run manifest."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: str
    pack: PackDef
    metrics: dict[str, MetricDef]
    content_sha256: str
