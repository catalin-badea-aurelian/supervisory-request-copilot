"""Files keep the original characters: UTF-8, never replaced or escaped.

The console replaces characters a legacy Windows console cannot print (workflow.console).
That must only affect terminal output, never the audit log, payloads, spec, manifest or
delivery file, which are evidence.
"""

from __future__ import annotations

import json

import copilot.workflow.console  # noqa: F401  (installs errors="replace" on stdout/stderr)
from tests.workflow.conftest import Answers, s2_draft, script_for

TEXT = "Rückfrage → Taux ≥ 0,5 % – “bis Freitag” – 中文 ​"


def _files(run_dir):
    yield run_dir / "audit.jsonl"
    yield run_dir / "spec.json"
    yield run_dir / "manifest.json"
    yield run_dir / "delivery.md"
    yield from sorted((run_dir / "payloads").glob("*.json"))


def test_files_are_utf8_with_original_characters(make_runtime, run):
    rt, _ = make_runtime(script_for(s2_draft(title=TEXT), ["period", "merchant_country"]))
    state = run(rt, Answers(), request="Chargeback rate Jul-Aug 2025. " + TEXT)
    assert state["status"] == "DELIVERED"
    run_dir = rt.run_dir("req-test")
    found = {}
    for path in _files(run_dir):
        raw = path.read_bytes()
        text = raw.decode("utf-8")  # strict: raises on anything that is not UTF-8
        assert "�" not in text, f"{path.name} contains a replacement character"
        assert "\\u2192" not in text and "\\u00fc" not in text, f"{path.name} escapes non-ASCII"
        assert b"\r\n" not in raw, f"{path.name} has CRLF line endings"
        if TEXT in text:
            found[path.name] = True
    # the request text and the spec title reach every evidence file that carries them
    for name in ("audit.jsonl", "spec.json", "manifest.json", "delivery.md"):
        assert found.get(name), f"{name} does not contain the original text"
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    assert manifest["spec"]["title"] == TEXT
