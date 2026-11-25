"""V-DUAL-01: preferred and independent paths agree, after the same key normalisation."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_dual_01 import evaluate

INFO = {
    "dims": ["period", "merchant_country"],
    "primary_path": "preferred",
    "other_path": "independent",
    "unknown_country": "XX",
}
THR = {"relative": 0.001}


def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["period", "merchant_country", "vol", "rate"])


BASE = [("2025-09-01", "DE", 100.0, 0.01), ("2025-09-01", "XX", 5.0, 0.02)]


def test_equal_results_pass() -> None:
    out = evaluate(Evidence({"primary": _frame(BASE), "other": _frame(BASE)}, INFO), THR)
    assert out.status == "PASS"


def test_null_country_on_one_side_equals_xx_on_the_other() -> None:
    other = _frame([BASE[0], ("2025-09-01", None, 5.0, 0.02)])
    assert evaluate(Evidence({"primary": _frame(BASE), "other": other}, INFO), THR).status == "PASS"
    empty = _frame([BASE[0], ("2025-09-01", "", 5.0, 0.02)])
    assert evaluate(Evidence({"primary": _frame(BASE), "other": empty}, INFO), THR).status == "PASS"


def test_doubled_slice_fails_on_that_slice_only() -> None:
    primary = _frame([("2025-09-01", "DE", 200.0, 0.01), BASE[1]])
    out = evaluate(Evidence({"primary": primary, "other": _frame(BASE)}, INFO), THR)
    assert out.status == "FAIL"
    assert out.slices == [{"merchant_country": "DE", "period": "2025-09-01", "status": "FAIL"}]


def test_row_on_one_side_only_fails() -> None:
    primary = _frame([*BASE, ("2025-09-01", "FR", 1.0, 0.0)])
    out = evaluate(Evidence({"primary": primary, "other": _frame(BASE)}, INFO), THR)
    assert [s["merchant_country"] for s in out.slices] == ["FR"]


def test_zero_and_undefined_ratios_on_both_sides_agree() -> None:
    rows = [("2025-09-01", "LU", 0.0, float("nan"))]
    out = evaluate(Evidence({"primary": _frame(rows), "other": _frame(rows)}, INFO), THR)
    assert out.status == "PASS"


def test_no_dimensions_compares_the_single_total() -> None:
    info = {**INFO, "dims": []}
    a = pd.DataFrame({"vol": [100.0]})
    b = pd.DataFrame({"vol": [100.05]})
    assert evaluate(Evidence({"primary": a, "other": b}, info), THR).status == "PASS"
    c = pd.DataFrame({"vol": [110.0]})
    out = evaluate(Evidence({"primary": a, "other": c}, info), THR)
    assert out.slices == [{"line": "total", "status": "FAIL"}]
