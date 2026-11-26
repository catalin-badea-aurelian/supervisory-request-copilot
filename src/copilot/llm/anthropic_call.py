"""The one place that sends a request to the Anthropic Messages API.

- structured output through `output_config.format` (JSON schema from the Pydantic model)
- depth through `output_config.effort`; no temperature (rejected by current models, ADR 015)
- the system prompt carries a cache breakpoint: instructions are the stable prefix
- no server-side fallbacks: a refusal comes back as stop_reason "refusal" and escalates
- transient errors (429, 5xx, timeouts) are retried by the SDK (`max_retries`)
"""

from __future__ import annotations

from typing import Any

from copilot.llm.transport import LlmRequest, LlmResponse, TransportError, Usage


def call_messages(client: Any, request: LlmRequest) -> LlmResponse:
    import anthropic

    try:
        response = client.messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": role, "content": text} for role, text in request.messages],
            output_config={
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.schema},
            },
        )
    except anthropic.APIStatusError as e:
        raise TransportError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise TransportError(f"Anthropic API not reachable: {e}") from e
    text = "".join(getattr(b, "text", "") for b in response.content if b.type == "text")
    u = response.usage
    return LlmResponse(
        text=text,
        stop_reason=str(response.stop_reason),
        model=str(response.model),
        usage=Usage(
            input_tokens=int(u.input_tokens or 0),
            output_tokens=int(u.output_tokens or 0),
            cache_read_input_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        ),
        request_id=getattr(response, "_request_id", None),
    )


def retrieve_model(client: Any, model_id: str) -> dict[str, Any]:
    """Live check that a configured model id exists (copilot llm check-models)."""
    m = client.models.retrieve(model_id)
    return {"id": m.id, "display_name": getattr(m, "display_name", None)}


def check_models(settings: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """`copilot llm check-models`: retrieve both configured ids from the live API."""
    import anthropic

    from copilot.llm.transport import live_calls_allowed

    if not live_calls_allowed():
        raise TransportError("live LLM calls are disabled in CI")
    client = anthropic.Anthropic()
    return [
        (var, model, retrieve_model(client, model))
        for var, model in (
            ("COPILOT_MODEL_MAIN", settings.model_main),
            ("COPILOT_MODEL_FAST", settings.model_fast),
        )
    ]
