"""How a request reaches a model: live, recorded, or replayed (ADR 015).

- `LiveTransport` calls the Anthropic API (the only module that imports the SDK).
- `RecordingTransport` calls live and stores each response under its request key.
- `ReplayTransport` answers only from recordings; a missing recording is an error, never a
  silent live call.

A recording holds the response text, stop reason, served model and usage. Never headers,
keys or credentials. The request key is the SHA-256 of the canonical request (model, prompt
ref, system, messages, output schema, effort, max_tokens), so changing a prompt invalidates
its recordings.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmRequest:
    model: str
    prompt_ref: str
    system: str
    messages: tuple[tuple[str, str], ...]  # (role, text)
    schema: dict[str, Any]
    effort: str
    max_tokens: int

    def key(self) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "prompt_ref": self.prompt_ref,
                "system": self.system,
                "messages": [list(m) for m in self.messages],
                "schema": self.schema,
                "effort": self.effort,
                "max_tokens": self.max_tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True)
class LlmResponse:
    text: str
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None
    replayed: bool = False


class Transport(Protocol):
    mode: str

    def send(self, request: LlmRequest) -> LlmResponse: ...


class TransportError(RuntimeError):
    pass


class RecordingMissing(TransportError):
    pass


def live_calls_allowed() -> bool:
    """Live calls are never allowed in CI (and tests replace the client anyway)."""
    return not os.environ.get("CI")


class LiveTransport:
    mode = "live"

    def __init__(self, timeout_s: float = 120.0, max_retries: int = 2, client: Any = None) -> None:
        if not live_calls_allowed():
            raise TransportError("live LLM calls are disabled in CI")
        if client is None:
            import anthropic

            client = anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)
        self._client = client

    def send(self, request: LlmRequest) -> LlmResponse:
        from copilot.llm.anthropic_call import call_messages

        return call_messages(self._client, request)


def _recording_path(directory: Path, key: str) -> Path:
    return directory / f"{key}.json"


class RecordingTransport:
    mode = "record"

    def __init__(self, directory: Path, live: Transport | None = None) -> None:
        self.directory = directory
        self._live = live or LiveTransport()

    def send(self, request: LlmRequest) -> LlmResponse:
        response = self._live.send(request)
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "key": request.key(),
            "prompt_ref": request.prompt_ref,
            "model_requested": request.model,
            "response": {
                "text": response.text,
                "stop_reason": response.stop_reason,
                "model": response.model,
                "usage": asdict(response.usage),
            },
        }
        _recording_path(self.directory, request.key()).write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return response


class ReplayTransport:
    mode = "replay"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def send(self, request: LlmRequest) -> LlmResponse:
        path = _recording_path(self.directory, request.key())
        if not path.is_file():
            raise RecordingMissing(
                f"no recording for {request.prompt_ref} (key {request.key()[:12]}…) in "
                f"{self.directory}; re-record this scenario with --record"
            )
        r = json.loads(path.read_text(encoding="utf-8"))["response"]
        return LlmResponse(
            text=r["text"],
            stop_reason=r["stop_reason"],
            model=r["model"],
            usage=Usage(**r["usage"]),
            request_id=None,
            replayed=True,
        )


def make_transport(
    mode: str, recordings: Path | None, *, timeout_s: float = 120.0, max_retries: int = 2
) -> Transport:
    if mode == "replay":
        if recordings is None:
            raise TransportError("replay mode needs a recordings directory")
        return ReplayTransport(recordings)
    if mode == "record":
        if recordings is None:
            raise TransportError("record mode needs a recordings directory")
        return RecordingTransport(recordings, LiveTransport(timeout_s, max_retries))
    if mode == "live":
        return LiveTransport(timeout_s, max_retries)
    raise TransportError(f"unknown LLM mode {mode!r} (replay, record or live)")
