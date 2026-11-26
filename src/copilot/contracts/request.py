"""Intake contracts: what the Intake agent returns per turn, and the confirmed RequestSpec.

The agent returns an `IntakeTurn` (a draft plus questions). Code validates the draft against
the domain pack and decides whether the specification is complete (ARCHITECTURE section 4).
Questions carry a `topic`, so scripted answers (demo) and code checks can match them without
depending on the wording the model chose.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Grain = Literal["month", "quarter"]
OutputFormat = Literal["table", "table+narrative"]
QuestionTopic = Literal[
    "metric_variant",  # e.g. value-based or count-based rate
    "attribution",  # month of the transaction or of the chargeback
    "perimeter",  # which countries (or all in scope)
    "as_of",  # data cut-off date
    "grain",  # monthly or quarterly
    "period",  # reporting period
    "output_format",  # table or table + narrative
    "precedent",  # overlap with an earlier request or submission
    "other",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(_Strict):
    topic: QuestionTopic
    text: str


class QAPair(_Strict):
    topic: QuestionTopic
    question: str
    answer: str
    actor: str  # who answered (e.g. "requester", "scripted-demo")


class Filter(_Strict):
    """A dimension restricted to values (a list, not a map, for structured outputs)."""

    dimension: str
    values: list[str]


class SpecDraft(_Strict):
    """The agent's current understanding. Every field may still be missing."""

    title: str | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    grain: Grain | None = None
    as_of_date: date | None = None
    output_format: OutputFormat | None = None
    deadline: date | None = None
    assumptions: list[str] = Field(default_factory=list)


class IntakeTurn(_Strict):
    """One Intake answer: the draft, the open questions, and the routing signal."""

    draft: SpecDraft
    questions: list[Question] = Field(default_factory=list)
    undefined_measures: list[str] = Field(default_factory=list)
    undefined_metric: bool = False  # a signal only: code validates it and decides
    precedent_refs: list[str] = Field(default_factory=list)
    reasoning: str = ""  # short, for the audit log


class Period(_Strict):
    start: date
    end: date
    grain: Grain


class RequestSpec(_Strict):
    """The confirmed specification (ARCHITECTURE section 6). Built and validated by code."""

    request_id: str
    title: str
    requester: str
    raw_request: str
    metrics: list[str]
    dimensions: list[str]
    filters: dict[str, list[str]]
    period: Period
    as_of_date: date
    output_format: OutputFormat
    deadline: date | None = None
    precedent_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)  # empty to pass G1
    undefined_measures: list[str] = Field(default_factory=list)  # non-empty -> proposal
