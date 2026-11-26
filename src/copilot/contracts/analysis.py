"""Analyst contracts: the plan the agent proposes and what code executed (ARCHITECTURE 6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from copilot.contracts.request import Filter

SourcePath = Literal["preferred", "independent"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryPlanItem(_Strict):
    metric_id: str
    dimensions: list[str]
    filters: list[Filter] = Field(default_factory=list)
    source_path: SourcePath = "preferred"
    non_template: bool = False  # rejected in M4 (escalates); free SQL is a later milestone
    rationale: str = ""


class QueryPlan(_Strict):
    """The Analyst's output. Code checks it against the confirmed spec before compiling."""

    items: list[QueryPlanItem]
    rationale: str = ""


class QueryRun(_Strict):
    metric_id: str
    metric_version: str
    template_id: str | None  # None means non-template
    non_template: bool = False
    rationale: str | None = None
    source_path: SourcePath
    sql: str
    sql_sha256: str
    params: dict[str, Any]
    snapshot_id: str
    bytes_processed: int | None = None
    result_sha256: str
    rows: int
    result_ref: str  # parquet file, relative to the run folder


class AnalysisResult(_Strict):
    request_id: str
    runs: list[QueryRun]
    source_path: SourcePath
