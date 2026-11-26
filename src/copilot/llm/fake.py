"""Scripted transport for unit tests: answers by prompt id, in order. Never touches a network."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from copilot.llm.transport import LlmRequest, LlmResponse, Usage


class ScriptedTransport:
    mode = "fake"

    def __init__(
        self,
        script: dict[str, list[Any]],
        *,
        model: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self._model = model
        self._usage = usage or Usage(input_tokens=1000, output_tokens=200)
        self.requests: list[LlmRequest] = []
        self.calls: dict[str, int] = defaultdict(int)

    def send(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        pid = request.prompt_ref.split("@", 1)[0]
        queue = self._script.get(pid)
        if not queue:
            raise AssertionError(f"no scripted response left for {pid}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]  # the last answer repeats
        self.calls[pid] += 1
        if isinstance(item, LlmResponse):
            return item
        text = item.model_dump_json() if isinstance(item, BaseModel) else str(item)
        return LlmResponse(
            text=text, stop_reason="end_turn", model=self._model or request.model, usage=self._usage
        )
