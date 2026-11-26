"""Committed LLM recordings: no secrets, and S1/S2 replay end to end without the API.

The leakage test runs in the default suite. The end-to-end replay needs the full-scale
dataset (the recorded explanations quote full-scale figures), so it is marked slow.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from copilot.audit.log import verify_chain
from copilot.config import Settings
from copilot.llm.gateway import Gateway
from copilot.llm.transport import ReplayTransport
from copilot.packs.loader import load_pack
from copilot.workflow.graph import Runtime
from copilot.workflow.rerun import rerun
from copilot.workflow.runner import drive
from copilot.workflow.scripted import ANSWERS_DIR, RECORDINGS_DIR, Script

RECORDINGS = sorted(RECORDINGS_DIR.glob("*/*.json"))
LEAK = re.compile(
    r"sk-ant|api[_-]?key|x-api-key|authorization|bearer|anthropic-version|request[_-]?id|"
    r"req_[a-z0-9]{6}|set-cookie|iam\.gserviceaccount|[A-Za-z]:\\\\Users",
    re.IGNORECASE,
)


def test_recordings_exist_for_both_scenarios():
    assert {p.parent.name for p in RECORDINGS} >= {"s1", "s2"}


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: f"{p.parent.name}/{p.stem[:8]}")
def test_recording_holds_only_response_fields_and_no_secrets(path: Path):
    text = path.read_text("utf-8")
    assert not LEAK.search(text), LEAK.search(text)
    rec = json.loads(text)
    assert set(rec) == {"key", "model_requested", "prompt_ref", "response"}
    assert set(rec["response"]) == {"model", "stop_reason", "text", "usage"}
    assert rec["key"] == path.stem


@pytest.mark.slow
@pytest.mark.parametrize("name", ["s1", "s2"])
def test_demo_scenario_replays_end_to_end(name, full_default, tmp_path):
    data = tmp_path / "parquet"
    shutil.copytree(full_default.parquet_dir, data)
    settings = replace(Settings(), backend="duckdb", parquet_dir=data, runs_dir=tmp_path / "runs")
    script = Script.load(ANSWERS_DIR / f"{name}.yaml")
    rt = Runtime(settings, load_pack(), Gateway(settings, ReplayTransport(script.recordings)))
    state = drive(rt, f"req-{name}", script, raw_request=script.request, requester=script.requester)
    assert state["status"] == script.expected["status"]
    assert verify_chain(rt.run_dir(f"req-{name}")).ok
    assert rerun(rt, f"req-{name}").ok
