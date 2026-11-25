"""Shared acceptance helpers: request shapes, the validation suite, and expectations.

Expectations come from tests/fixtures/defects_expected.yaml (test-only). Nothing here is
imported by the copilot package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from copilot.contracts.validation import ValidationReport
from copilot.packs.compiler import MetricRequest
from copilot.packs.models import DomainPack
from copilot.validation.runner import run_checks
from copilot.validation.target import prepare_target

AS_OF = date(2025, 10, 15)
START, END = date(2024, 10, 1), date(2025, 9, 30)
COUNTRY_MONTH = ("period", "merchant_country")
ALL_DIMS = ("period", "merchant_country", "mcc_group")

# WARNs a defect may raise besides its own expected detections: movements (V-VAR-01), the
# chargeback maturity window (V-MAT-01) and allocation differences next to an explicit
# unallocated (XX) line (V-REC-01).
ALLOWED_COLLATERAL_WARNS = {"V-VAR-01", "V-MAT-01", "V-REC-01"}
CLEAN_WARNS = {"V-VAR-01", "V-MAT-01"}


def shapes(pack: DomainPack) -> dict[str, list[MetricRequest]]:
    """The DQ-scan sweep (every metric x month x country) and the scenario shapes."""
    sweep = [MetricRequest(m, START, END, AS_OF, "month", COUNTRY_MONTH) for m in pack.metrics]
    return {
        "sweep": sweep,
        "s1": [
            MetricRequest(
                "chargeback_rate_value",
                date(2025, 1, 1),
                date(2025, 6, 30),
                AS_OF,
                "quarter",
                ALL_DIMS,
            )
        ],
        "s2": [
            MetricRequest(
                "chargeback_rate_value",
                date(2025, 7, 1),
                date(2025, 8, 31),
                AS_OF,
                "month",
                COUNTRY_MONTH,
            )
        ],
        "r1": [
            MetricRequest(
                "chargeback_rate_value",
                date(2025, 4, 1),
                date(2025, 7, 31),
                AS_OF,
                "month",
                ALL_DIMS,
                {"merchant_country": ("IT",)},
            )
        ],
    }


def run_suite(
    pack: DomainPack, backend: Any, resolver: Any = None, only: tuple[str, ...] | None = None
) -> dict[str, ValidationReport]:
    out = {}
    for name, reqs in shapes(pack).items():
        if only and name not in only:
            continue
        targets = [prepare_target(pack, r, backend, resolver, request_id=name) for r in reqs]
        out[name] = run_checks(targets, pack, backend, resolver, request_id=f"acceptance-{name}")
    return out


@dataclass(frozen=True)
class Finding:
    shape: str
    metric_id: str
    check_id: str
    status: str
    slice: tuple[tuple[str, str], ...]

    @property
    def s(self) -> dict[str, str]:
        return dict(self.slice)


def findings(reports: dict[str, ValidationReport]) -> list[Finding]:
    out = []
    for shape, rep in reports.items():
        for c in rep.checks:
            for s in c.affected_slices:
                key = tuple(sorted((k, v) for k, v in s.items() if k != "status"))
                out.append(Finding(shape, c.metric_id, c.check_id, s["status"], key))
    return out


def ids(fs: list[Finding], status: str) -> set[str]:
    return {f.check_id for f in fs if f.status == status}


def expected(manifest: dict, defect: str) -> tuple[set[str], set[str]]:
    det = manifest["defects"][defect]["detections"]
    return (
        {k for k, v in det.items() if v == "FAIL"},
        {k for k, v in det.items() if v == "WARN"},
    )


def in_defect_slice(manifest: dict, defect: str, s: dict[str, str]) -> bool:
    """Is a FAIL slice inside the defect's documented slice?"""
    spec = manifest["defects"][defect]
    period = s.get("period")
    if period is None:
        return False
    if defect == "D1":
        sl = spec["slice"]
        country_ok = s.get("merchant_country", sl["merchant_country"]) == sl["merchant_country"]
        return period == str(sl["month"]) and country_ok
    if defect == "D2":
        return period == str(spec["onboarded_month"])
    if defect == "D3":
        return period == str(spec["month"])
    if defect == "D4":
        return period >= str(spec["from_month"])
    raise KeyError(defect)


def fails_by_month(fs: list[Finding]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in fs:
        if f.status == "FAIL":
            out.setdefault(f.s["period"][:7], set()).add(f.check_id)
    return out


def comparable(reports: dict[str, ValidationReport]) -> dict:
    """Statuses and slices per (shape, metric, check): what must match across backends."""
    return {
        (shape, c.metric_id, c.check_id): (c.status, c.affected_slices)
        for shape, rep in reports.items()
        for c in rep.checks
    }
