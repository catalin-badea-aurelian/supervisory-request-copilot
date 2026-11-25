"""CheckResult and ValidationReport: canonical values and a stable report hash."""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from copilot.contracts.validation import CheckResult, ValidationReport, canonical_value


def _result(check_id: str = "V-REC-01", status: str = "FAIL", **kw) -> CheckResult:
    base = dict(
        check_id=check_id,
        check_name="x",
        metric_id="m",
        status=status,
        principle_refs=[7, 3],
        measured={"v": np.float64(0.1 + 0.2), "n": np.int64(3), "d": date(2025, 8, 1)},
        threshold={"relative": 0.005},
        affected_slices=[
            {"period": pd.Timestamp("2025-08-01"), "merchant_country": "ES", "status": "FAIL"},
            {"period": date(2025, 8, 1), "merchant_country": "ES", "status": "FAIL"},
        ],
    )
    base.update(kw)
    return CheckResult.build(**base)


def test_canonical_values() -> None:
    assert canonical_value(np.float64(0.1 + 0.2)) == 0.3
    assert canonical_value(Decimal("1.50")) == 1.5
    assert canonical_value(float("nan")) is None
    assert canonical_value(pd.Timestamp("2025-08-01")) == "2025-08-01"
    assert canonical_value(-0.0) == 0.0


def test_slices_are_canonical_deduplicated_and_sorted() -> None:
    r = _result()
    assert r.affected_slices == [
        {"merchant_country": "ES", "period": "2025-08-01", "status": "FAIL"}
    ]
    assert r.principle_refs == [3, 7]


def test_report_hash_is_order_independent_and_verifiable() -> None:
    a, b = _result("V-PK-01"), _result("V-REC-01")
    r1 = ValidationReport.build("REQ-1", [a, b])
    r2 = ValidationReport.build("REQ-1", [b, a])
    assert r1.report_sha256 == r2.report_sha256
    assert r1.verify_hash()
    assert r1.status == "FAIL" and len(r1.fails()) == 2
    tampered = r1.model_copy(deep=True)
    tampered.checks[0].status = "PASS"
    assert not tampered.verify_hash()


def test_report_round_trips_through_json() -> None:
    r = ValidationReport.build("REQ-1", [_result(), _result("V-VAR-01", "WARN")])
    again = ValidationReport.model_validate_json(r.model_dump_json())
    assert again == r and again.verify_hash()
