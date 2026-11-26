"""The single LLM gateway (ARCHITECTURE section 5, ADR 015).

    parsed, record = gateway.call("intake", data, IntakeTurn, ctx)

- the model id comes from config through the prompt's tier (never from code or a prompt)
- the output is parsed into a Pydantic model; on a validation error the model gets one
  retry with the error, then `LlmEscalation` (the workflow routes it to the next gate)
- `refusal` and `max_tokens` stop reasons escalate; nothing is coerced silently
- a per-run token budget is checked before a call (spent + max_tokens) and after it
- every call is written to the audit log with prompt id and version, model, tokens and cost
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from copilot.config import Settings
from copilot.llm.pricing import Pricing
from copilot.llm.prompts import Prompt, load_prompt
from copilot.llm.schema import output_schema
from copilot.llm.transport import LlmRequest, LlmResponse, Transport, Usage

T = TypeVar("T", bound=BaseModel)


class LlmEscalation(RuntimeError):
    """The model did not produce a usable answer; a human gate must decide."""

    def __init__(self, prompt_ref: str, reason: str, detail: str = "") -> None:
        super().__init__(f"{prompt_ref}: {reason}" + (f" ({detail})" if detail else ""))
        self.prompt_ref, self.reason, self.detail = prompt_ref, reason, detail


class BudgetExceeded(LlmEscalation):
    pass


@dataclass
class RunBudget:
    limit_tokens: int
    spent_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def check_before(self, prompt: Prompt) -> None:
        if self.spent_tokens + prompt.max_tokens > self.limit_tokens:
            raise BudgetExceeded(
                prompt.ref,
                "token budget",
                f"spent {self.spent_tokens:,} + max {prompt.max_tokens:,} > {self.limit_tokens:,}",
            )

    def add(self, usage: Usage, cost: float | None) -> None:
        self.calls += 1
        self.spent_tokens += usage.total
        self.tokens_in += (
            usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
        )
        self.tokens_out += usage.output_tokens
        self.cost_usd += cost or 0.0


@dataclass
class CallContext:
    budget: RunBudget
    audit: Any = None  # AuditLog | None
    actor: str = "agent"


@dataclass(frozen=True)
class LlmCallRecord:
    prompt_id: str
    prompt_version: str
    model_requested: str
    model_served: str
    attempts: int
    usage: Usage
    cost_usd: float | None
    request_keys: tuple[str, ...]
    replayed: bool
    stop_reason: str
    extra: dict[str, Any] = field(default_factory=dict)


def as_data(payload: Any) -> str:
    """Wrap request content as data: the model is told to treat it as data, not instructions."""
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    return f"<data>\n{body}\n</data>"


class Gateway:
    def __init__(self, settings: Settings, transport: Transport, pricing: Pricing | None = None):
        self.settings = settings
        self.transport = transport
        self.pricing = pricing or Pricing.load()

    def model_for(self, prompt: Prompt) -> str:
        return self.settings.model_main if prompt.tier == "main" else self.settings.model_fast

    def call(
        self, prompt_name: str, data: Any, output_model: type[T], ctx: CallContext
    ) -> tuple[T, LlmCallRecord]:
        prompt = load_prompt(prompt_name)
        model = self.model_for(prompt)
        schema = output_schema(output_model)
        messages: list[tuple[str, str]] = [("user", as_data(data))]
        usage_total = Usage()
        keys: list[str] = []
        cost_total: float | None = 0.0
        response: LlmResponse | None = None
        error = ""
        for attempt in (1, 2):
            ctx.budget.check_before(prompt)
            request = LlmRequest(
                model=model,
                prompt_ref=prompt.ref,
                system=prompt.system,
                messages=tuple(messages),
                schema=schema,
                effort=prompt.effort,
                max_tokens=prompt.max_tokens,
            )
            keys.append(request.key())
            response = self.transport.send(request)
            cost = self.pricing.cost_usd(response.model, response.usage)
            ctx.budget.add(response.usage, cost)
            usage_total = _sum(usage_total, response.usage)
            cost_total = None if cost is None or cost_total is None else cost_total + cost
            if response.stop_reason == "refusal":
                self._audit(
                    ctx, prompt, model, response, attempt, keys, usage_total, cost_total, "refusal"
                )
                raise LlmEscalation(prompt.ref, "the model declined the request")
            if response.stop_reason == "max_tokens":
                self._audit(
                    ctx,
                    prompt,
                    model,
                    response,
                    attempt,
                    keys,
                    usage_total,
                    cost_total,
                    "max_tokens",
                )
                raise LlmEscalation(prompt.ref, "output truncated at max_tokens")
            try:
                parsed = output_model.model_validate_json(response.text)
            except ValidationError as e:
                error = _short_errors(e)
                if attempt == 2:
                    self._audit(
                        ctx,
                        prompt,
                        model,
                        response,
                        attempt,
                        keys,
                        usage_total,
                        cost_total,
                        "invalid_output",
                        error,
                    )
                    raise LlmEscalation(
                        prompt.ref, "output failed validation twice", error
                    ) from None
                messages += [
                    ("assistant", response.text),
                    (
                        "user",
                        "Your answer did not validate against the schema:\n"
                        + error
                        + "\nReturn a corrected answer for the same data.",
                    ),
                ]
                continue
            record = LlmCallRecord(
                prompt_id=prompt.id,
                prompt_version=prompt.version,
                model_requested=model,
                model_served=response.model,
                attempts=attempt,
                usage=usage_total,
                cost_usd=cost_total,
                request_keys=tuple(keys),
                replayed=response.replayed,
                stop_reason=response.stop_reason,
            )
            self._audit(
                ctx,
                prompt,
                model,
                response,
                attempt,
                keys,
                usage_total,
                cost_total,
                "ok",
                error,
                parsed.model_dump(mode="json"),
            )
            return parsed, record
        raise AssertionError("unreachable")  # pragma: no cover

    def _audit(
        self,
        ctx: CallContext,
        prompt: Prompt,
        model: str,
        response: LlmResponse,
        attempts: int,
        keys: list[str],
        usage: Usage,
        cost: float | None,
        outcome: str,
        error: str = "",
        output: Any = None,
    ) -> None:
        if ctx.audit is None:
            return
        ctx.audit.append(
            "llm_call",
            actor=ctx.actor,
            role="maker" if prompt.tier == "main" else "explainer",
            model_id=response.model,
            prompt_id=prompt.ref,
            summary=f"{prompt.ref} {outcome} ({attempts} attempt{'s' if attempts > 1 else ''})",
            payload={
                "prompt_id": prompt.id,
                "prompt_version": prompt.version,
                "model_requested": model,
                "model_served": response.model,
                "outcome": outcome,
                "attempts": attempts,
                "stop_reason": response.stop_reason,
                "request_keys": keys,
                "replayed": response.replayed,
                "usage": usage.__dict__,
                "cost_usd": cost,
                "pricing_status": self.pricing.status,
                "validation_error": error or None,
                "output": output,
            },
        )


def _sum(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.output_tokens + b.output_tokens,
        a.cache_read_input_tokens + b.cache_read_input_tokens,
        a.cache_creation_input_tokens + b.cache_creation_input_tokens,
    )


def _short_errors(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:10]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)
