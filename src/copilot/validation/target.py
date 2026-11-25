"""What the validation engine checks: one governed metric result and how it was produced."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from copilot.packs.compiler import CompiledQuery, MetricRequest, SourcePath, compile_metric
from copilot.packs.models import DomainPack
from copilot.validation.backend import ReadBackend


@dataclass(frozen=True)
class ValidationTarget:
    """The maker's output for one metric.

    `primary` is the query whose result is delivered. After a G2 decision to deliver from the
    independent path, `primary.path` is "independent" and the checks validate that result.
    """

    request_id: str
    request: MetricRequest
    primary: CompiledQuery
    primary_result: pd.DataFrame
    non_template: bool = False

    @property
    def primary_path(self) -> SourcePath:
        return self.primary.path


def prepare_target(
    pack: DomainPack,
    req: MetricRequest,
    backend: ReadBackend,
    resolver: Any = None,
    path: SourcePath = "preferred",
    request_id: str = "adhoc",
) -> ValidationTarget:
    """Compile and run one governed query, and wrap it for validation."""
    q = compile_metric(pack, req, path, backend.dialect, resolver)  # type: ignore[arg-type]
    return ValidationTarget(request_id, req, q, backend.query(q.sql, q.params))
