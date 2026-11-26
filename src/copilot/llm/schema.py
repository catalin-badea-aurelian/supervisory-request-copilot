"""JSON schema sent as `output_config.format` for a Pydantic output model.

Uses the SDK's own `transform_schema` (the same transform `messages.parse` applies), so the
schema is accepted by structured outputs. The schema is part of the replay key, so it must be
deterministic: it is built once per model class and serialised with sorted keys.

The schema is generated in serialization mode with every field required (nullable where the
field may be empty). A model then cannot "answer" by omitting fields: in a live test the
Intake model put the whole spec into `title` and left the optional fields out.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from pydantic import BaseModel


@cache
def _schema_json(model: type[BaseModel]) -> str:
    from anthropic import transform_schema  # pure function, no network

    raw = model.model_json_schema(mode="serialization")
    return json.dumps(transform_schema(_require_all(raw)), sort_keys=True)


def _require_all(node: Any) -> Any:
    """Mark every property of every object as required (defaults stay in Python only)."""
    if isinstance(node, dict):
        out = {k: _require_all(v) for k, v in node.items()}
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out["required"] = sorted(out["properties"])
        for prop in (out.get("properties") or {}).values():
            if isinstance(prop, dict):
                prop.pop("default", None)
        return out
    if isinstance(node, list):
        return [_require_all(v) for v in node]
    return node


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_schema_json(model))
    return result
