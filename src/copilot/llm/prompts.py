"""Versioned prompts: `src/copilot/prompts/<name>.md` with YAML front-matter.

    ---
    id: intake
    version: 1.0.0
    tier: main          # main -> COPILOT_MODEL_MAIN, fast -> COPILOT_MODEL_FAST
    effort: medium      # output_config.effort
    max_tokens: 4000
    ---
    <system prompt>

The body is the system prompt (instructions only). Request-specific content is sent by code
as delimited data in the user message, never mixed into the instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
Tier = Literal["main", "fast"]
Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    tier: Tier
    effort: Effort
    max_tokens: int
    system: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class PromptError(ValueError):
    pass


@cache
def load_prompt(name: str, prompt_dir: Path = PROMPT_DIR) -> Prompt:
    path = prompt_dir / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise PromptError(f"{path.name}: missing front-matter")
    _, head, body = text.split("---\n", 2)
    meta = yaml.safe_load(head) or {}
    for key in ("id", "version", "tier", "effort", "max_tokens"):
        if key not in meta:
            raise PromptError(f"{path.name}: front-matter lacks {key!r}")
    if meta["id"] != name:
        raise PromptError(f"{path.name}: id {meta['id']!r} does not match the file name")
    if meta["tier"] not in ("main", "fast"):
        raise PromptError(f"{path.name}: tier must be main or fast")
    return Prompt(
        id=str(meta["id"]),
        version=str(meta["version"]),
        tier=meta["tier"],
        effort=meta["effort"],
        max_tokens=int(meta["max_tokens"]),
        system=body.strip() + "\n",
    )
