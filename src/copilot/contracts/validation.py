"""Validation contracts: one CheckResult per (metric, check), collected in a ValidationReport.

Everything in a result is JSON-safe and canonical (floats rounded, dates as ISO strings), so
the report hash is stable across runs, operating systems and backends.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["PASS", "WARN", "FAIL"]
STATUS_RANK: dict[str, int] = {"PASS": 0, "WARN": 1, "FAIL": 2}
FLOAT_PRECISION = 10


def worst(statuses: list[str]) -> Status:
    """The most severe status; PASS for an empty list."""
    out: Status = "PASS"
    for s in statuses:
        if STATUS_RANK[s] > STATUS_RANK[out]:
            out = s  # type: ignore[assignment]
    return out


def canonical_value(v: Any) -> Any:
    """Convert a value to its canonical JSON-safe form (used for measured values and slices)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):  # numpy scalar
        try:
            v = v.item()
        except (AttributeError, ValueError):
            pass
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, (float, Decimal)):
        f = float(v)
        if math.isnan(f):
            return None
        r = round(f, FLOAT_PRECISION)
        return 0.0 if r == 0 else r
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "isoformat"):  # pandas Timestamp
        return canonical_value(v.to_pydatetime())
    if isinstance(v, dict):
        return {str(k): canonical_value(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [canonical_value(x) for x in v]
    return str(v)


def _slice_key(s: dict[str, str]) -> str:
    return json.dumps(s, sort_keys=True)


class CheckResult(BaseModel):
    """Outcome of one check for one metric (ARCHITECTURE section 6).

    Every affected slice carries its own `status` (WARN or FAIL), so a result that is FAIL in
    one month and WARN in another stays readable slice by slice (and exports one row per
    check x slice to spb_ops).
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    check_name: str
    metric_id: str
    status: Status
    principle_refs: list[int]
    measured: dict[str, Any] = Field(default_factory=dict)
    threshold: dict[str, Any] = Field(default_factory=dict)
    affected_slices: list[dict[str, str]] = Field(default_factory=list)
    explanation: str | None = None  # LLM-written in later milestones, labelled as such
    accepted_ref: str | None = None  # G2 decision id when the finding was already decided

    @classmethod
    def build(
        cls,
        *,
        check_id: str,
        check_name: str,
        metric_id: str,
        status: Status,
        principle_refs: list[int],
        measured: dict[str, Any],
        threshold: dict[str, Any],
        affected_slices: list[dict[str, Any]],
    ) -> CheckResult:
        slices = [{k: str(canonical_value(v)) for k, v in s.items()} for s in affected_slices]
        slices = sorted({_slice_key(s): s for s in slices}.values(), key=_slice_key)
        return cls(
            check_id=check_id,
            check_name=check_name,
            metric_id=metric_id,
            status=status,
            principle_refs=sorted(principle_refs),
            measured=canonical_value(measured),
            threshold=canonical_value(threshold),
            affected_slices=slices,
        )

    def slices_with(self, status: Status) -> list[dict[str, str]]:
        return [s for s in self.affected_slices if s.get("status") == status]


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    checks: list[CheckResult]
    report_sha256: str

    @staticmethod
    def compute_hash(request_id: str, checks: list[CheckResult]) -> str:
        payload = {
            "request_id": request_id,
            "checks": [c.model_dump(mode="json") for c in checks],
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def build(cls, request_id: str, checks: list[CheckResult]) -> ValidationReport:
        ordered = sorted(checks, key=lambda c: (c.metric_id, c.check_id))
        return cls(
            request_id=request_id,
            checks=ordered,
            report_sha256=cls.compute_hash(request_id, ordered),
        )

    def verify_hash(self) -> bool:
        return self.report_sha256 == self.compute_hash(self.request_id, self.checks)

    def fails(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "FAIL"]

    def warns(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "WARN"]

    @property
    def status(self) -> Status:
        return worst([c.status for c in self.checks])
