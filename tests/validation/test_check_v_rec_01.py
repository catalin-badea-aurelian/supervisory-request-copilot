"""V-REC-01: reconciliation per month x country with an explicit unallocated (XX) line."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_rec_01 import evaluate

MEASURE = "settled_volume_eur"
LEDGER = {"DE": 1000.0, "FR": 500.0, "ES": 400.0}
INFO = {
    "applicable": True,
    "measures": [MEASURE],
    "scope": ["DE", "ES", "FR"],
    "unknown_country": "XX",
}
THR = {"relative": 0.005}


def _ev(result: dict[str, float], scope: list[str] | None = None) -> Evidence:
    res = pd.DataFrame(
        {"period": "2025-09-01", "merchant_country": list(result), MEASURE: list(result.values())}
    )
    led = pd.DataFrame(
        {
            "period": "2025-09-01",
            "merchant_country": list(LEDGER),
            "measure": MEASURE,
            "amount_eur": list(LEDGER.values()),
        }
    )
    info = {**INFO, "scope": scope or INFO["scope"]}
    return Evidence({"result": res, "ledger": led}, info)


def test_reconciled_month_passes() -> None:
    assert evaluate(_ev(dict(LEDGER)), THR).status == "PASS"


def test_shortfalls_covered_by_unallocated_xx_warn_with_explicit_line() -> None:
    out = evaluate(_ev({"DE": 980.0, "FR": 490.0, "ES": 400.0, "XX": 30.0}), THR)
    assert out.status == "WARN"
    assert {"period": "2025-09-01", "line": "unallocated", "status": "WARN"} in out.slices
    assert {s["merchant_country"] for s in out.slices if "merchant_country" in s} == {"DE", "FR"}
    lines = {line["line"]: line for line in out.measured["lines"]}
    assert lines["unallocated"]["result"] == 30.0
    assert lines["total"]["result"] == lines["total"]["ledger"] == 1900.0


def test_duplicated_country_is_an_excess_and_fails() -> None:
    out = evaluate(_ev({"DE": 1000.0, "FR": 500.0, "ES": 800.0}), THR)
    assert out.status == "FAIL"
    assert out.slices == [{"merchant_country": "ES", "period": "2025-09-01", "status": "FAIL"}]


def test_incomplete_load_fails_every_short_country() -> None:
    out = evaluate(_ev({"DE": 600.0, "FR": 300.0, "ES": 240.0}), THR)
    assert out.status == "FAIL"
    assert {s["merchant_country"] for s in out.slices} == {"DE", "ES", "FR"}


def test_shortfall_larger_than_unallocated_fails() -> None:
    out = evaluate(_ev({"DE": 900.0, "FR": 500.0, "ES": 400.0, "XX": 10.0}), THR)
    assert out.status == "FAIL"


def test_out_of_scope_break_is_reported_on_the_total_line_only() -> None:
    out = evaluate(_ev({"DE": 1000.0, "FR": 500.0, "ES": 800.0}, scope=["DE"]), THR)
    assert out.status == "WARN"
    assert out.slices == [{"line": "total", "period": "2025-09-01", "status": "WARN"}]


def test_within_tolerance_passes() -> None:
    assert evaluate(_ev({"DE": 1004.0, "FR": 500.0, "ES": 400.0}), THR).status == "PASS"


def test_not_applicable_for_metrics_without_ledger() -> None:
    assert evaluate(Evidence({}, {"applicable": False}), THR).status == "PASS"
