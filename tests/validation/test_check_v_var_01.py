"""V-VAR-01: period-over-period movements beyond the threshold are WARN, never FAIL."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_var_01 import evaluate

INFO = {
    "dims": ["period", "merchant_country"],
    "value": "m",
    "first_period": "2025-08-01",
    "unknown_country": "XX",
}
THR = {"relative": 0.25}


def _frame(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["2025-07-01", "2025-08-01", "2025-09-01"],
            "merchant_country": ["IT"] * 3,
            "m": values,
        }
    )


def test_stable_series_passes() -> None:
    assert evaluate(Evidence({"result": _frame([1.0, 1.1, 1.0])}, INFO), THR).status == "PASS"


def test_large_movement_warns_on_the_moving_period() -> None:
    out = evaluate(Evidence({"result": _frame([1.0, 1.0, 2.0])}, INFO), THR)
    assert out.status == "WARN"
    assert out.slices == [{"merchant_country": "IT", "period": "2025-09-01", "status": "WARN"}]


def test_movement_into_the_first_requested_period_counts_but_not_before() -> None:
    out = evaluate(Evidence({"result": _frame([1.0, 3.0, 3.0])}, INFO), THR)
    assert [s["period"] for s in out.slices] == ["2025-08-01"]


def test_zero_or_missing_base_is_skipped() -> None:
    out = evaluate(Evidence({"result": _frame([0.0, 5.0, float("nan")])}, INFO), THR)
    assert out.status == "PASS"


def test_spike_never_fails() -> None:
    out = evaluate(Evidence({"result": _frame([0.001, 1000.0, 0.001])}, INFO), THR)
    assert out.status == "WARN"
