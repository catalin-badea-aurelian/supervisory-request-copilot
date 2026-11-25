"""Check framework: a registry of checks, each split into an I/O step and a pure step.

- `gather(ctx) -> Evidence` runs read-only queries through the backend: governed metric
  templates (recompiled at the grain the check needs) and the engine's own control-table SQL.
- `evaluate(evidence, thresholds) -> Outcome` is a pure function of the evidence and the
  thresholds from the domain pack. Unit tests call it on hand-built frames.

Every affected slice carries `status` (WARN or FAIL).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from copilot.contracts.validation import Status
from copilot.packs.compiler import CompiledQuery, MetricRequest, SourcePath, compile_metric
from copilot.packs.models import DomainPack, MetricDef
from copilot.validation.backend import ReadBackend
from copilot.validation.sql import render_check_sql
from copilot.validation.target import ValidationTarget


@dataclass
class Evidence:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    status: Status
    measured: dict[str, Any]
    slices: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckContext:
    pack: DomainPack
    metric: MetricDef
    target: ValidationTarget
    backend: ReadBackend
    resolver: Any = None

    @property
    def dialect(self) -> str:
        return self.backend.dialect

    @property
    def req(self) -> MetricRequest:
        return self.target.request

    @property
    def unknown_country(self) -> str:
        return self.pack.pack.unknown_country_code

    def variant(self, **changes: Any) -> MetricRequest:
        return dataclasses.replace(self.req, **changes)

    def compile(self, req: MetricRequest, path: SourcePath | None = None) -> CompiledQuery:
        return compile_metric(
            self.pack,
            req,
            path or self.target.primary_path,
            self.dialect,  # type: ignore[arg-type]
            self.resolver,
        )

    def run_metric(self, req: MetricRequest, path: SourcePath | None = None) -> pd.DataFrame:
        q = self.compile(req, path)
        return self.backend.query(q.sql, q.params)

    def run_sql(
        self, template: str, params: dict[str, Any] | None = None, **context: Any
    ) -> pd.DataFrame:
        base = {
            "period_start": self.req.period_start,
            "period_end": self.req.period_end,
            "as_of": self.req.as_of,
        }
        base.update(params or {})
        sql, used = render_check_sql(
            self.pack, template, self.dialect, self.resolver, base, **context
        )
        return self.backend.query(sql, used)

    def sources_read(self) -> list[str]:
        """Union of the sources read by the preferred and the independent query."""
        out = set(self.target.primary.sources)
        for path in ("preferred", "independent"):
            out |= set(self.compile(self.req, path).sources)
        return sorted(out)


Gather = Callable[[CheckContext], Evidence]
Evaluate = Callable[[Evidence, dict[str, float]], Outcome]


@dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    principles: tuple[int, ...]
    default_severity: str  # "FAIL", "WARN" or "WARN/FAIL" (catalogue, ARCHITECTURE 9.1)
    gather: Gather
    evaluate: Evaluate
    max_status: Status = "FAIL"  # WARN-only checks can never FAIL


REGISTRY: dict[str, CheckSpec] = {}


def register(spec: CheckSpec) -> CheckSpec:
    if spec.id in REGISTRY:
        raise ValueError(f"duplicate check id {spec.id}")
    REGISTRY[spec.id] = spec
    return spec
