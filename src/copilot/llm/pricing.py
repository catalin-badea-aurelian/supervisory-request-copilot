"""Cost accounting from `config/llm_pricing.yaml` (USD per million tokens)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from copilot.config import REPO_ROOT
from copilot.llm.transport import Usage

PRICING_FILE = REPO_ROOT / "config" / "llm_pricing.yaml"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True)
class Pricing:
    prices: dict[str, Price]
    status: str
    verified_on: str | None

    @classmethod
    def load(cls, path: Path = PRICING_FILE) -> Pricing:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        prices = {m: Price(**p) for m, p in raw["usd_per_mtok"].items()}
        verified = raw.get("verified_on")
        return cls(prices, str(raw.get("status", "to_verify")), str(verified) if verified else None)

    def cost_usd(self, model: str, usage: Usage) -> float | None:
        p = self.prices.get(model)
        if p is None:
            return None  # unknown model: reported as None, never guessed
        return (
            usage.input_tokens * p.input
            + usage.output_tokens * p.output
            + usage.cache_read_input_tokens * p.cache_read
            + usage.cache_creation_input_tokens * p.cache_write
        ) / 1_000_000
