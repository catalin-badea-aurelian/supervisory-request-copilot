"""Append-only, hash-chained audit log (ARCHITECTURE section 12.1).

    runs/<request_id>/audit.jsonl          one event per line
    runs/<request_id>/payloads/NNNN-*.json the event's payload, referenced by SHA-256

event_sha256 = sha256(prev_event_sha256 + canonical_json(event without event_sha256)); the
first event chains from GENESIS. `verify_chain` recomputes every link and the payload hashes
and reports the first break.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot.contracts.validation import canonical_value

GENESIS = "0" * 64
EVENT_TYPES = frozenset(
    {
        "request_received",
        "question_asked",
        "answer_received",
        "spec_drafted",
        "gate_decision",
        "llm_call",
        "tool_call",
        "query_executed",
        "check_result",
        "validation_completed",
        "retrieval",
        "citation_validated",
        "evidence_built",
        "pack_updated",
        "status_changed",
        "escalation",
        "snapshot_taken",
        "scan_started",
        "ops_exported",
    }
)


def canonical_json(obj: Any) -> str:
    return json.dumps(
        canonical_value(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditError(RuntimeError):
    pass


class AuditLog:
    """Writes one run's audit log. Opening an existing log continues its chain."""

    def __init__(self, run_dir: Path, request_id: str) -> None:
        self.run_dir = run_dir
        self.request_id = request_id
        self.path = run_dir / "audit.jsonl"
        self.payload_dir = run_dir / "payloads"
        self._seq = 0
        self._prev = GENESIS
        self._sync()

    def _sync(self) -> None:
        """Continue from the last event on disk (several writers may share one run's log)."""
        if not self.path.is_file():
            return
        with self.path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            chunk = min(size, 65536)
            fh.seek(size - chunk)
            tail = fh.read().decode("utf-8").splitlines()
        if tail:
            last = json.loads(tail[-1])
            self._seq, self._prev = int(last["seq"]), str(last["event_sha256"])

    @property
    def head(self) -> str:
        self._sync()
        return self._prev

    def append(
        self,
        event_type: str,
        *,
        actor: str,
        role: str,
        payload: Any = None,
        model_id: str | None = None,
        prompt_id: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise AuditError(f"unknown audit event type {event_type!r}")
        self._sync()
        self._seq += 1
        payload_ref = payload_sha = None
        if payload is not None:
            body = canonical_json(payload)
            payload_sha = sha256_text(body)
            payload_ref = f"payloads/{self._seq:04d}-{event_type}.json"
            self.payload_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / payload_ref).write_text(body, encoding="utf-8", newline="\n")
        event: dict[str, Any] = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "request_id": self.request_id,
            "event_type": event_type,
            "actor": actor,
            "role": role,
            "model_id": model_id,
            "prompt_id": prompt_id,
            "summary": summary,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha,
            "prev_event_sha256": self._prev,
        }
        event["event_sha256"] = sha256_text(self._prev + canonical_json(event))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        self._prev = event["event_sha256"]
        return event


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    events: int
    head: str
    first_bad_seq: int | None = None
    reason: str = ""


def verify_chain(run_dir: Path) -> ChainReport:
    path = run_dir / "audit.jsonl"
    if not path.is_file():
        return ChainReport(False, 0, GENESIS, None, "no audit log")
    prev = GENESIS
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return ChainReport(False, n - 1, prev, n, "line is not valid JSON")
        seq = event.get("seq")
        if seq != n:
            return ChainReport(False, n - 1, prev, n, f"sequence gap (found seq {seq})")
        if event.get("prev_event_sha256") != prev:
            return ChainReport(False, n - 1, prev, n, "prev_event_sha256 does not match")
        claimed = event.pop("event_sha256", None)
        if sha256_text(prev + canonical_json(event)) != claimed:
            return ChainReport(False, n - 1, prev, n, "event content was changed")
        ref = event.get("payload_ref")
        if ref:
            p = run_dir / ref
            if not p.is_file():
                return ChainReport(False, n - 1, prev, n, f"payload missing: {ref}")
            if sha256_text(p.read_text(encoding="utf-8")) != event.get("payload_sha256"):
                return ChainReport(False, n - 1, prev, n, f"payload changed: {ref}")
        prev = str(claimed)
    return ChainReport(True, len(lines), prev)


def read_payload(run_dir: Path, event: dict[str, Any]) -> Any:
    ref = event.get("payload_ref")
    return json.loads((run_dir / ref).read_text(encoding="utf-8")) if ref else None
