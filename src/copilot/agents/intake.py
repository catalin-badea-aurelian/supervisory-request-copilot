"""Intake agent: clarify a request until the specification is complete (ARCHITECTURE section 5).

The model drafts the specification and asks questions; code then reviews the draft against
the domain pack and adds the questions the model missed. Code, not the model, decides that the
specification is complete. The agent has no data access: it sees the glossary (definition
view), the dimension values and the metadata of earlier submissions.

Rules enforced in code:
- every metric is in the glossary and approved; otherwise a question, or the metric-proposal
  route when the model reports an undefined measure
- dimensions and filter values are allowed for every metric; the period aligns to the grain
  and lies inside the data range
- the as-of date and the perimeter come from the requester, never from an assumption: they
  must appear in the request text or in an answer on that topic
- an overlap with an earlier submission is raised as a precedent question
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from typing import Any

from copilot.agents.precedent import Submission, overlapping, submission_index
from copilot.contracts.request import (
    Filter,
    IntakeTurn,
    Period,
    QAPair,
    Question,
    RequestSpec,
    SpecDraft,
)
from copilot.llm.gateway import CallContext, Gateway
from copilot.packs.models import DomainPack
from copilot.packs.views import glossary_context

MAX_TURNS = 5


@dataclass
class IntakeOutcome:
    turn: IntakeTurn
    questions: list[Question]  # what is asked next (empty: complete)
    spec: RequestSpec | None = None
    undefined_measures: list[str] = field(default_factory=list)


def _answered(qa: list[QAPair], topic: str) -> bool:
    return any(p.topic == topic for p in qa)


def _mentioned(text: str, qa: list[QAPair], value: str) -> bool:
    return value.lower() in text.lower() or any(value.lower() in p.answer.lower() for p in qa)


def _aligned(d: SpecDraft) -> str | None:
    if d.period_start is None or d.period_end is None or d.grain is None:
        return None
    step = 1 if d.grain == "month" else 3
    s, e = d.period_start, d.period_end
    if s > e:
        return "the period start is after its end"
    if s.day != 1 or (s.month - 1) % step:
        return f"the period start {s} is not the first day of a {d.grain}"
    if e.day != calendar.monthrange(e.year, e.month)[1] or e.month % step:
        return f"the period end {e} is not the last day of a {d.grain}"
    return None


def review_draft(
    pack: DomainPack,
    draft: SpecDraft,
    qa: list[QAPair],
    raw_request: str,
    submissions: list[Submission],
) -> list[Question]:
    """Questions code requires before the specification can go to G1."""
    qs: list[Question] = []
    p = pack.pack

    def ask(topic: Any, text: str) -> None:
        qs.append(Question(topic=topic, text=text))

    metrics = [pack.metrics[m] for m in draft.metrics if m in pack.metrics]
    unknown = [mid for mid in draft.metrics if mid not in pack.metrics]
    if not draft.metrics:
        ask("metric_variant", "Which governed metric should be reported?")
    for mid in unknown:
        ask("metric_variant", f"'{mid}' is not a metric in the glossary. Which metric is meant?")
    for m in metrics:
        if m.status != "approved":
            ask("metric_variant", f"'{m.id}' is not approved (status {m.status}). Which metric?")
    for dim in draft.dimensions:
        for m in metrics:
            if dim not in m.allowed_dimensions:
                ask("other", f"'{dim}' is not an allowed breakdown of {m.id}. Drop it or change?")
    for f in draft.filters:
        allowed = p.dimensions.get(f.dimension)
        if allowed is None or any(f.dimension not in m.allowed_dimensions for m in metrics):
            ask("perimeter", f"'{f.dimension}' cannot be used as a filter. Which perimeter?")
        elif allowed.values and (bad := [v for v in f.values if v not in allowed.values]):
            ask("perimeter", f"Unknown {f.dimension} value(s) {bad}. Which values are meant?")
    if draft.grain is None:
        ask("grain", "Monthly or quarterly figures?")
    if draft.period_start is None or draft.period_end is None:
        ask("period", "Which reporting period (first and last month)?")
    elif (problem := _aligned(draft)) is not None:
        ask("period", f"The period does not fit the granularity: {problem}. Please confirm.")
    elif draft.period_start < p.data_range["start"] or draft.period_end > p.data_range["end"]:
        ask("period", f"Data covers {p.data_range['start']} to {p.data_range['end']}. Period?")
    if draft.as_of_date is None or not (
        _answered(qa, "as_of") or draft.as_of_date.isoformat() in raw_request
    ):
        ask("as_of", "Which as-of date (data cut-off) should the figures use?")
    country = next((f for f in draft.filters if f.dimension == "merchant_country"), None)
    stated = country is not None and all(_mentioned(raw_request, [], v) for v in country.values)
    if not (_answered(qa, "perimeter") or stated):
        ask("perimeter", "All merchant countries in scope, or a subset?")
    if draft.output_format is None:
        ask("output_format", "A table only, or a table with a short narrative?")
    if draft.period_start and draft.period_end and not _answered(qa, "precedent"):
        hits = overlapping(submissions, draft.metrics, draft.period_start, draft.period_end)
        if hits:
            ids = ", ".join(sorted(f"{h.metric_id} {h.period} ({h.ids})" for h in hits))
            ask(
                "precedent",
                f"Overlaps earlier submission(s) {ids}. Must the figures stay consistent with "
                "them, or explain differences?",
            )
    return qs


def _key(q: Question) -> str:
    return f"other:{q.text}" if q.topic == "other" else q.topic


def merge_questions(
    agent: list[Question], required: list[Question], qa: list[QAPair]
) -> list[Question]:
    """One question per topic. Code's required questions win; the model's questions on topics
    already answered are dropped, so the loop cannot ask the same thing twice."""
    out = {_key(q): q for q in required}
    covered = {q.topic for q in required}
    for q in agent:
        if q.topic in covered or (q.topic != "other" and _answered(qa, q.topic)):
            continue
        out.setdefault(_key(q), q)
    return list(out.values())


def build_spec(
    pack: DomainPack,
    draft: SpecDraft,
    *,
    request_id: str,
    requester: str,
    raw_request: str,
    precedent_refs: list[str],
) -> RequestSpec:
    assert draft.period_start and draft.period_end and draft.grain and draft.as_of_date
    dims = list(draft.dimensions)
    if "period" not in dims:
        dims.insert(0, "period")
    filters: dict[str, list[str]] = {}
    for f in draft.filters:
        filters[f.dimension] = sorted(set(filters.get(f.dimension, [])) | set(f.values))
    return RequestSpec(
        request_id=request_id,
        title=draft.title or raw_request[:80],
        requester=requester,
        raw_request=raw_request,
        metrics=list(dict.fromkeys(draft.metrics)),
        dimensions=dims,
        filters=dict(sorted(filters.items())),
        period=Period(start=draft.period_start, end=draft.period_end, grain=draft.grain),
        as_of_date=draft.as_of_date,
        output_format=draft.output_format or "table",
        deadline=draft.deadline,
        precedent_refs=sorted(set(precedent_refs)),
        assumptions=draft.assumptions,
    )


class IntakeAgent:
    prompt = "intake"

    def __init__(self, gateway: Gateway, pack: DomainPack, submissions: list[Submission]):
        self.gateway, self.pack, self.submissions = gateway, pack, submissions

    def inputs(self, raw_request: str, qa: list[QAPair], feedback: list[str]) -> dict[str, Any]:
        return {
            "glossary": glossary_context(self.pack),
            "earlier_submissions": submission_index(self.submissions),
            "request": raw_request,
            "answers_so_far": [p.model_dump(mode="json", exclude={"actor"}) for p in qa],
            "reviewer_feedback": feedback,
        }

    def step(
        self,
        raw_request: str,
        qa: list[QAPair],
        ctx: CallContext,
        *,
        request_id: str,
        requester: str,
        feedback: list[str] | None = None,
    ) -> IntakeOutcome:
        data = self.inputs(raw_request, qa, feedback or [])
        turn, _ = self.gateway.call(self.prompt, data, IntakeTurn, ctx)
        undefined = [m for m in turn.undefined_measures if m not in self.pack.metrics]
        if undefined:
            # code confirms the signal: a measure the glossary does not define -> proposal flow
            return IntakeOutcome(turn, [], None, undefined)
        required = review_draft(self.pack, turn.draft, qa, raw_request, self.submissions)
        questions = merge_questions(turn.questions, required, qa)
        if questions:
            return IntakeOutcome(turn, questions)
        refs = list(turn.precedent_refs)
        if turn.draft.period_start and turn.draft.period_end:
            hits = overlapping(
                self.submissions, turn.draft.metrics, turn.draft.period_start, turn.draft.period_end
            )
            refs += [h.ref for h in hits]
        known = {s.ref for s in self.submissions}
        spec = build_spec(
            self.pack,
            turn.draft,
            request_id=request_id,
            requester=requester,
            raw_request=raw_request,
            precedent_refs=[r for r in refs if r in known],
        )
        return IntakeOutcome(turn, [], spec)


__all__ = ["MAX_TURNS", "Filter", "IntakeAgent", "IntakeOutcome", "build_spec", "review_draft"]
