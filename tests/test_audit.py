"""Hash-chained audit log: chaining, tamper detection, several writers, manifest round trip."""

from __future__ import annotations

import json

import pytest

from copilot.audit.log import GENESIS, AuditError, AuditLog, verify_chain
from copilot.audit.manifest import RunManifest, read_manifest, write_manifest


def _log(tmp_path, n=3):
    log = AuditLog(tmp_path, "req-a")
    for i in range(n):
        log.append("tool_call", actor="t", role="system", payload={"i": i}, summary=f"e{i}")
    return log


def test_chain_verifies_and_starts_from_genesis(tmp_path):
    _log(tmp_path)
    first = json.loads((tmp_path / "audit.jsonl").read_text("utf-8").splitlines()[0])
    assert first["prev_event_sha256"] == GENESIS and first["seq"] == 1
    rep = verify_chain(tmp_path)
    assert rep.ok and rep.events == 3


def test_two_writers_share_one_chain(tmp_path):
    a, b = AuditLog(tmp_path, "req-a"), AuditLog(tmp_path, "req-a")
    a.append("tool_call", actor="a", role="system")
    b.append("tool_call", actor="b", role="system")
    a.append("tool_call", actor="a", role="system")
    assert verify_chain(tmp_path).ok and verify_chain(tmp_path).events == 3


def test_edited_event_is_detected(tmp_path):
    _log(tmp_path)
    path = tmp_path / "audit.jsonl"
    lines = path.read_text("utf-8").splitlines()
    ev = json.loads(lines[1])
    ev["summary"] = "changed"
    lines[1] = json.dumps(ev, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", "utf-8")
    rep = verify_chain(tmp_path)
    assert not rep.ok and rep.first_bad_seq == 2 and "changed" in rep.reason


def test_edited_payload_is_detected(tmp_path):
    _log(tmp_path)
    payload = sorted((tmp_path / "payloads").glob("*.json"))[0]
    payload.write_text('{"i":99}', "utf-8")
    rep = verify_chain(tmp_path)
    assert not rep.ok and "payload changed" in rep.reason


def test_deleted_event_is_detected(tmp_path):
    _log(tmp_path)
    path = tmp_path / "audit.jsonl"
    lines = path.read_text("utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", "utf-8")
    assert not verify_chain(tmp_path).ok


def test_unknown_event_type_is_refused(tmp_path):
    with pytest.raises(AuditError):
        AuditLog(tmp_path, "req-a").append("made_up", actor="x", role="y")


def test_missing_log_does_not_verify(tmp_path):
    assert not verify_chain(tmp_path).ok


def test_manifest_round_trip(tmp_path):
    m = RunManifest(
        request_id="req-a",
        status="DELIVERED",
        pack_id="payments",
        pack_version="1.2.0",
        pack_content_sha256="x" * 64,
        backend="duckdb",
    )
    write_manifest(tmp_path, m)
    assert read_manifest(tmp_path) == m
