"""Prompt governance: a prompt change needs a version bump, and the recordings must follow.

Replay is keyed on the canonical request, which includes prompt id@version and the system
text. So any prompt change makes replay fail loudly with a re-record message. These tests
check that, and the lock catches a prompt edited without a version bump before replay does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from copilot.config import Settings
from copilot.llm import gateway as gateway_module
from copilot.llm.gateway import CallContext, Gateway, RunBudget
from copilot.llm.prompts import PROMPT_DIR, load_prompt
from copilot.llm.transport import (
    LlmResponse,
    RecordingMissing,
    RecordingTransport,
    ReplayTransport,
    Usage,
)
from copilot.workflow.scripted import RECORDINGS_DIR

LOCK = yaml.safe_load((Path(__file__).parent / "fixtures" / "prompt_lock.yaml").read_text("utf-8"))
PROMPTS = sorted(p.stem for p in PROMPT_DIR.glob("*.md"))


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    likely_cause: str
    impact: str


GOOD = '{"summary": "s", "likely_cause": "c", "impact": "i"}'


class FakeLive:
    mode = "live"

    def send(self, request):
        return LlmResponse(GOOD, "end_turn", request.model, Usage(10, 5))


def _call(transport, monkeypatch=None, prompt=None):
    if prompt is not None:
        monkeypatch.setattr(gateway_module, "load_prompt", lambda name: prompt)
    gw = Gateway(Settings(), transport)
    return gw.call("failure_explainer", {"x": 1}, Out, CallContext(RunBudget(10**6)))


def test_replay_of_the_recorded_prompt_succeeds(tmp_path):
    _call(RecordingTransport(tmp_path, FakeLive()))
    out, rec = _call(ReplayTransport(tmp_path))
    assert rec.replayed and out.summary == "s"


def test_prompt_version_bump_fails_replay_loudly(tmp_path, monkeypatch):
    _call(RecordingTransport(tmp_path, FakeLive()))
    bumped = replace(load_prompt("failure_explainer"), version="9.9.9")
    with pytest.raises(RecordingMissing) as e:
        _call(ReplayTransport(tmp_path), monkeypatch, bumped)
    assert "failure_explainer@9.9.9" in str(e.value) and "--record" in str(e.value)


def test_prompt_text_change_without_version_bump_also_fails_replay(tmp_path, monkeypatch):
    _call(RecordingTransport(tmp_path, FakeLive()))
    p = load_prompt("failure_explainer")
    edited = replace(p, system=p.system + "One more instruction.\n")
    with pytest.raises(RecordingMissing, match="--record"):
        _call(ReplayTransport(tmp_path), monkeypatch, edited)


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_body_matches_the_lock_for_its_version(name):
    p = load_prompt(name)
    digest = hashlib.sha256(p.system.encode("utf-8")).hexdigest()
    assert p.ref in LOCK, (
        f"{p.ref} is not in tests/fixtures/prompt_lock.yaml: add it and re-record the demo "
        "recordings (copilot demo --record)"
    )
    assert LOCK[p.ref] == digest, (
        f"{name} changed without a version bump: bump `version` in its front-matter, add the "
        "new id@version to tests/fixtures/prompt_lock.yaml and re-record (copilot demo --record)"
    )


def test_committed_recordings_match_current_prompt_versions():
    current = {load_prompt(n).id: load_prompt(n).ref for n in PROMPTS}
    stale = []
    for path in sorted(RECORDINGS_DIR.glob("*/*.json")):
        ref = json.loads(path.read_text("utf-8"))["prompt_ref"]
        if current.get(ref.split("@")[0]) != ref:
            stale.append(f"{path.parent.name}/{path.name[:12]}: {ref}")
    assert not stale, f"stale recordings, re-record with copilot demo --record: {stale}"


def test_cli_demo_reports_a_missing_recording_and_exits_2(tmp_path, monkeypatch, small_default):
    from typer.testing import CliRunner

    from copilot import cli
    from copilot.workflow import scripted

    monkeypatch.setattr(scripted, "RECORDINGS_DIR", tmp_path / "empty")  # no recordings at all
    monkeypatch.setenv("COPILOT_BACKEND", "duckdb")
    monkeypatch.setenv("COPILOT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("COPILOT_PARQUET_DIR", str(small_default.parquet_dir))
    result = CliRunner().invoke(cli.app, ["demo", "--scenario", "s2"])
    assert result.exit_code == 2
    assert "--record" in result.output and "intake@" in result.output
