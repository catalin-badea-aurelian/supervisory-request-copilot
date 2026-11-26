"""LLM gateway: parsing, retry then escalation, stop reasons, budget, cost, audit, replay."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from copilot.audit.log import AuditLog, read_payload, verify_chain
from copilot.config import Settings
from copilot.llm.fake import ScriptedTransport
from copilot.llm.gateway import BudgetExceeded, CallContext, Gateway, LlmEscalation, RunBudget
from copilot.llm.pricing import Price, Pricing
from copilot.llm.prompts import load_prompt
from copilot.llm.transport import (
    LiveTransport,
    LlmRequest,
    LlmResponse,
    RecordingMissing,
    RecordingTransport,
    ReplayTransport,
    TransportError,
    Usage,
    make_transport,
)


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    likely_cause: str
    impact: str


GOOD = '{"summary": "s", "likely_cause": "c", "impact": "i"}'
SETTINGS = replace(Settings(), model_main="main-model-x", model_fast="fast-model-y")
PRICING = Pricing({"fast-model-y": Price(1.0, 10.0, 0.1, 1.25)}, "to_verify", None)


def _gw(script: list, **kw) -> tuple[Gateway, ScriptedTransport]:
    t = ScriptedTransport({"failure_explainer": script}, **kw)
    return Gateway(SETTINGS, t, PRICING), t


def _ctx(limit: int = 100_000, audit=None) -> CallContext:
    return CallContext(budget=RunBudget(limit), audit=audit)


def test_model_comes_from_config_by_tier():
    gw, t = _gw([GOOD])
    gw.call("failure_explainer", {"x": 1}, Out, _ctx())
    assert t.requests[0].model == "fast-model-y"  # failure_explainer is tier fast
    assert gw.model_for(load_prompt("intake")) == "main-model-x"


def test_parses_into_the_model_and_wraps_input_as_data():
    gw, t = _gw([GOOD])
    out, rec = gw.call("failure_explainer", {"x": 1}, Out, _ctx())
    assert out == Out(summary="s", likely_cause="c", impact="i")
    assert rec.attempts == 1 and rec.prompt_id == "failure_explainer"
    assert t.requests[0].messages[0][1].startswith("<data>\n")


def test_one_retry_with_the_validation_error_then_success():
    gw, t = _gw(['{"summary": "s"}', GOOD])
    out, rec = gw.call("failure_explainer", {}, Out, _ctx())
    assert rec.attempts == 2 and out.impact == "i"
    retry_msgs = t.requests[1].messages
    assert retry_msgs[1][0] == "assistant" and "did not validate" in retry_msgs[2][1]
    assert "likely_cause" in retry_msgs[2][1]


def test_second_invalid_output_escalates_never_coerces():
    gw, _ = _gw(['{"summary": "s"}', "not json"])
    with pytest.raises(LlmEscalation, match="failed validation twice"):
        gw.call("failure_explainer", {}, Out, _ctx())


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_and_truncation_escalate(stop):
    resp = LlmResponse(text=GOOD, stop_reason=stop, model="fast-model-y", usage=Usage(10, 5))
    gw, t = _gw([resp])
    with pytest.raises(LlmEscalation):
        gw.call("failure_explainer", {}, Out, _ctx())
    assert len(t.requests) == 1  # no retry on a refusal or truncation


def test_budget_checked_before_the_call():
    gw, t = _gw([GOOD])
    max_tokens = load_prompt("failure_explainer").max_tokens
    with pytest.raises(BudgetExceeded):
        gw.call("failure_explainer", {}, Out, _ctx(limit=max_tokens - 1))
    assert t.requests == []  # hard stop: nothing was sent


def test_budget_counts_actual_usage_and_stops_later_calls():
    gw, _ = _gw([GOOD], usage=Usage(input_tokens=1500, output_tokens=500))
    ctx = _ctx(limit=3000)
    gw.call("failure_explainer", {}, Out, ctx)
    assert ctx.budget.spent_tokens == 2000 and ctx.budget.calls == 1
    with pytest.raises(BudgetExceeded):  # 2000 + max_tokens 800 fits, a third would not
        gw.call("failure_explainer", {}, Out, ctx)
        gw.call("failure_explainer", {}, Out, ctx)


def test_cost_from_the_price_table():
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000)
    assert PRICING.cost_usd("fast-model-y", usage) == pytest.approx(1.0 + 1.0 + 0.1)
    assert PRICING.cost_usd("unknown", usage) is None  # never guessed


def test_pricing_file_is_marked_to_verify_or_dated():
    p = Pricing.load()
    assert p.status in ("to_verify", "verified")
    if p.status == "verified":
        assert p.verified_on
    assert {"claude-sonnet-5-5", "claude-haiku-5-5"} <= set(p.prices)


def test_every_call_is_audited_with_prompt_id_and_version(tmp_path):
    audit = AuditLog(tmp_path, "req-t")
    gw, _ = _gw([GOOD])
    gw.call("failure_explainer", {}, Out, _ctx(audit=audit))
    lines = (tmp_path / "audit.jsonl").read_text("utf-8").splitlines()
    import json

    ev = json.loads(lines[-1])
    prompt = load_prompt("failure_explainer")
    assert ev["event_type"] == "llm_call" and ev["prompt_id"] == prompt.ref
    payload = read_payload(tmp_path, ev)
    assert payload["prompt_version"] == prompt.version
    assert payload["usage"]["input_tokens"] == 1000 and payload["cost_usd"] is not None
    assert verify_chain(tmp_path).ok


def test_prompts_have_front_matter_and_no_sampling_parameters():
    for name in ("intake", "analyst", "failure_explainer"):
        p = load_prompt(name)
        assert p.id == name and p.version and p.tier in ("main", "fast")
        assert "temperature" not in p.system.lower()
    assert load_prompt("failure_explainer").tier == "fast"  # narrative/explanations: fast model


def _request(**kw) -> LlmRequest:
    base = dict(
        model="m",
        prompt_ref="p@1",
        system="s",
        messages=(("user", "u"),),
        schema={"type": "object"},
        effort="low",
        max_tokens=10,
    )
    return LlmRequest(**(base | kw))


def test_request_key_is_stable_and_sensitive():
    assert _request().key() == _request().key()
    assert _request().key() != _request(system="s2").key()
    assert _request().key() != _request(prompt_ref="p@2").key()


def test_replay_missing_recording_fails_loudly(tmp_path):
    with pytest.raises(RecordingMissing, match="--record"):
        ReplayTransport(tmp_path).send(_request())


def test_record_then_replay_round_trip_without_secrets(tmp_path):
    class FakeLive:
        mode = "live"

        def send(self, request):
            return LlmResponse(GOOD, "end_turn", "m-served", Usage(3, 4), request_id="req_123")

    RecordingTransport(tmp_path, FakeLive()).send(_request())
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text("utf-8")
    assert "req_123" not in text and "api_key" not in text.lower()
    r = ReplayTransport(tmp_path).send(_request())
    assert r.replayed and r.text == GOOD and r.usage == Usage(3, 4) and r.model == "m-served"


def test_live_and_record_refused_in_ci(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(TransportError):
        LiveTransport()
    with pytest.raises(TransportError):
        make_transport("record", tmp_path)


def test_default_mode_is_replay():
    assert Settings().llm_mode == "replay"


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_llm_schemas_require_every_field():
    """A model must not answer by omitting fields (seen live: the spec packed into `title`)."""
    from copilot.agents.explainer import Explanation
    from copilot.contracts.analysis import QueryPlan
    from copilot.contracts.request import IntakeTurn
    from copilot.llm.schema import output_schema

    for model in (IntakeTurn, QueryPlan, Explanation):
        for obj in _objects(output_schema(model)):
            assert sorted(obj["required"]) == sorted(obj["properties"]), model.__name__
            assert obj.get("additionalProperties") is False
