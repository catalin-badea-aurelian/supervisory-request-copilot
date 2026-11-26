"""Analyst: the plan must match the spec; the agent sees definitions only."""

from __future__ import annotations

from datetime import date

import pytest

from copilot.agents.analyst import AnalystAgent, PlanRejected, check_plan
from copilot.config import Settings
from copilot.contracts.analysis import QueryPlan, QueryPlanItem
from copilot.contracts.request import Filter, Period, RequestSpec
from copilot.llm.fake import ScriptedTransport
from copilot.llm.gateway import CallContext, Gateway, RunBudget
from copilot.packs.views import glossary_context, metric_definition_view

SPEC = RequestSpec(
    request_id="r",
    title="t",
    requester="q",
    raw_request="x",
    metrics=["chargeback_rate_value"],
    dimensions=["period", "merchant_country"],
    filters={"merchant_country": ["DE", "ES"]},
    period=Period(start=date(2025, 7, 1), end=date(2025, 8, 31), grain="month"),
    as_of_date=date(2025, 10, 15),
    output_format="table",
)


def _item(**kw) -> QueryPlanItem:
    base = dict(
        metric_id="chargeback_rate_value",
        dimensions=["merchant_country", "period"],
        filters=[Filter(dimension="merchant_country", values=["ES", "DE"])],
    )
    return QueryPlanItem(**(base | kw))


def test_matching_plan_is_accepted():
    assert check_plan(SPEC, QueryPlan(items=[_item()])) == []


@pytest.mark.parametrize(
    "item,msg",
    [
        (dict(metric_id="chargeback_rate_count"), "differ from the spec"),
        (dict(dimensions=["period"]), "dimensions"),
        (dict(filters=[]), "filters"),
        (dict(non_template=True), "non-template"),
        (dict(source_path="independent"), "preferred"),
    ],
)
def test_plan_departing_from_the_spec_is_rejected(item, msg):
    errors = check_plan(SPEC, QueryPlan(items=[_item(**item)]))
    assert errors and any(msg in e for e in errors)


def test_duplicate_or_missing_items_are_rejected():
    assert check_plan(SPEC, QueryPlan(items=[_item(), _item()]))
    assert check_plan(SPEC, QueryPlan(items=[]))


def test_rejected_plan_escalates(pack):
    t = ScriptedTransport({"analyst": [QueryPlan(items=[_item(non_template=True)])]})
    with pytest.raises(PlanRejected):
        AnalystAgent(Gateway(Settings(), t), pack).plan(SPEC, CallContext(RunBudget(10**6)))


FORBIDDEN = (
    "tolerances",
    '"checks"',
    "ewi",
    "amber",
    "V-PK-01",
    "V-REC-01",
    "ledger_control_totals",
    "prior_submissions",
    "meta_load_log",
    "defect",
)


def test_definition_view_has_no_checker_material(pack):
    for mid in pack.metrics:
        view = metric_definition_view(pack, mid)
        assert set(view) & {"tolerances", "checks", "ewi", "reconciles_to"} == set()
    text = str(glossary_context(pack))
    for word in ("tolerances", "V-PK-01", "amber", "ledger_control_totals", "meta_load_log"):
        assert word not in text


def test_analyst_input_has_no_thresholds_control_tables_or_defects(pack):
    t = ScriptedTransport({"analyst": [QueryPlan(items=[_item()])]})
    AnalystAgent(Gateway(Settings(), t), pack).plan(SPEC, CallContext(RunBudget(10**6)))
    sent = t.requests[0].messages[0][1] + t.requests[0].system
    for word in FORBIDDEN:
        assert word not in sent, word
