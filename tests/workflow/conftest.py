"""Workflow fixtures: a runtime on the small generated dataset with a scripted LLM."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from copilot.agents.explainer import Explanation
from copilot.config import Settings
from copilot.contracts.analysis import QueryPlan, QueryPlanItem
from copilot.contracts.request import IntakeTurn, Question, SpecDraft
from copilot.llm.fake import ScriptedTransport
from copilot.llm.gateway import Gateway
from copilot.packs.loader import load_pack
from copilot.workflow.graph import Runtime
from copilot.workflow.runner import drive

S2_REQUEST = "The supervisor asks for the chargeback rate by merchant country for July-August 2025."
S1_REQUEST = "Chargeback rate by merchant country and merchant category for Q1-Q2 2025."
ANSWERS = {
    "metric_variant": "value-based",
    "as_of": "2025-10-15",
    "perimeter": "all countries",
    "output_format": "table",
    "precedent": "explain differences to the earlier submission",
    "attribution": "transaction month",
    "grain": "as stated",
    "period": "as stated",
    "other": "none",
}


def s2_draft(**kw: Any) -> SpecDraft:
    base = dict(
        metrics=["chargeback_rate_value"],
        dimensions=["merchant_country"],
        period_start=date(2025, 7, 1),
        period_end=date(2025, 8, 31),
        grain="month",
        as_of_date=date(2025, 10, 15),
        output_format="table",
        title="S2",
    )
    return SpecDraft(**(base | kw))


def s1_draft() -> SpecDraft:
    return s2_draft(
        dimensions=["merchant_country", "mcc_group"],
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        grain="quarter",
        title="S1",
    )


def script_for(draft: SpecDraft, dims: list[str]) -> dict[str, list[Any]]:
    return {
        "intake": [
            IntakeTurn(
                draft=draft.model_copy(update={"as_of_date": None}),
                questions=[Question(topic="metric_variant", text="value or count?")],
            ),
            IntakeTurn(draft=draft),
        ],
        "analyst": [
            QueryPlan(items=[QueryPlanItem(metric_id="chargeback_rate_value", dimensions=dims)])
        ],
        "failure_explainer": [Explanation(summary="s", likely_cause="c", impact="i")],
    }


class Answers:
    """Test answerer: records every pending interrupt; gate decisions from a dict of lists."""

    def __init__(self, gates: dict[str, list[Any]] | None = None, pause_at: str | None = None):
        self.gates = {k: list(v) for k, v in (gates or {}).items()}
        self.seen: list[dict[str, Any]] = []
        self.pause_at = pause_at

    def __call__(self, p: dict[str, Any]) -> Any:
        self.seen.append(p)
        if p["type"] == "questions":
            return [{"topic": q["topic"], "answer": ANSWERS[q["topic"]]} for q in p["questions"]]
        gate = p["gate"]
        if gate == self.pause_at:
            self.pause_at = None
            return None
        queue = self.gates.get(gate) or []
        if queue:
            d = queue.pop(0)
        else:
            d = {"G1": "approve", "G2": "independent_path", "G3": "approve"}.get(gate, "reject")
        d = d if isinstance(d, dict) else {"decision": d}
        if gate == "G3" and d["decision"] == "approve" and "acknowledged_warnings" not in d:
            d = {**d, "acknowledged_warnings": p["required_acknowledgements"]}
        return d

    @property
    def gate_sequence(self) -> list[str]:
        return [p.get("gate") or "questions" for p in self.seen]


@pytest.fixture
def dataset_copy(small_default, tmp_path):
    """A private copy of the small default dataset (tests may change files)."""
    dst = tmp_path / "parquet"
    shutil.copytree(small_default.parquet_dir, dst)
    return dst


@pytest.fixture
def make_runtime(dataset_copy, tmp_path):
    def make(script: dict[str, list[Any]], **settings_kw: Any) -> tuple[Runtime, ScriptedTransport]:
        settings = replace(
            Settings(),
            backend="duckdb",
            parquet_dir=dataset_copy,
            runs_dir=tmp_path / "runs",
            **settings_kw,
        )
        t = ScriptedTransport(script)
        return Runtime(settings, load_pack(), Gateway(settings, t)), t

    return make


@pytest.fixture
def run():
    def go(rt: Runtime, answers: Answers, request: str = S2_REQUEST, rid: str = "req-test"):
        return drive(rt, rid, answers, raw_request=request, requester="tester")

    return go
