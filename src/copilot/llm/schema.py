"""JSON schema sent as `output_config.format` for a Pydantic output model.

Uses the SDK's own `transform_schema` (the same transform `messages.parse` applies), so the
schema is accepted by structured outputs. The schema is part of the replay key, so it must be
deterministic: it is built once per model class and serialised with sorted keys.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from pydantic import BaseModel


@cache
def _schema_json(model: type[BaseModel]) -> str:
    from anthropic import transform_schema  # pure function, no network

    return json.dumps(transform_schema(model), sort_keys=True)


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_schema_json(model))
    return result
