"""Run each target metric's declared check set and build the ValidationReport (no LLM)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from copilot.contracts.validation import STATUS_RANK, CheckResult, ValidationReport
from copilot.packs.models import DomainPack
from copilot.validation.backend import ReadBackend
from copilot.validation.checks import REGISTRY
from copilot.validation.checks.base import CheckContext
from copilot.validation.target import ValidationTarget


class ValidationError(RuntimeError):
    pass


def thresholds_for(pack: DomainPack, metric_id: str, check_id: str) -> dict[str, float]:
    """Pack defaults, overridden by the metric's own tolerances. Never hard-coded in checks."""
    return {
        **pack.pack.defaults.get(check_id, {}),
        **pack.metrics[metric_id].tolerances.get(check_id, {}),
    }


def run_check(
    check_id: str,
    target: ValidationTarget,
    pack: DomainPack,
    backend: ReadBackend,
    resolver: Any = None,
) -> CheckResult:
    if check_id not in REGISTRY:
        raise ValidationError(f"unknown check {check_id}")
    spec = REGISTRY[check_id]
    metric = pack.metrics[target.request.metric_id]
    thresholds = thresholds_for(pack, metric.id, check_id)
    ctx = CheckContext(pack, metric, target, backend, resolver)
    outcome = spec.evaluate(spec.gather(ctx), thresholds)
    if STATUS_RANK[outcome.status] > STATUS_RANK[spec.max_status]:
        raise ValidationError(f"{check_id} returned {outcome.status}; max is {spec.max_status}")
    return CheckResult.build(
        check_id=spec.id,
        check_name=spec.name,
        metric_id=metric.id,
        status=outcome.status,
        principle_refs=list(spec.principles),
        measured=outcome.measured,
        threshold=thresholds,
        affected_slices=outcome.slices,
    )


def run_checks(
    targets: Iterable[ValidationTarget],
    pack: DomainPack,
    backend: ReadBackend,
    resolver: Any = None,
    request_id: str | None = None,
) -> ValidationReport:
    targets = list(targets)
    if not targets:
        raise ValidationError("nothing to validate")
    rid = request_id or targets[0].request_id
    results = [
        run_check(check_id, t, pack, backend, resolver)
        for t in targets
        for check_id in pack.metrics[t.request.metric_id].checks
    ]
    return ValidationReport.build(rid, results)
