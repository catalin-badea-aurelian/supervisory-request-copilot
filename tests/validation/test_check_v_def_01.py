"""V-DEF-01: a published pre-computed column equals the glossary formula."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_def_01 import evaluate

INFO = {"applicable": True, "unknown_country": "XX"}
THR = {"absolute": 1e-9}
NAME = "grp_merchant_monthly.cb_rate"


def _rows(published: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["2025-08-01", "2025-09-01", "2025-09-01"],
            "merchant_country": ["DE", "DE", "FR"],
            "published": published,
            "numerator": [3.0, 4.0, 0.0],
            "denominator": [1000.0, 800.0, 0.0],
        }
    )


def test_conforming_column_passes() -> None:
    df = _rows([0.003, 0.005, float("nan")])
    assert evaluate(Evidence({NAME: df}, INFO), THR).status == "PASS"


def test_drifted_rows_fail_per_month_and_country() -> None:
    df = _rows([0.003, 0.0123, float("nan")])
    out = evaluate(Evidence({NAME: df}, INFO), THR)
    assert out.status == "FAIL"
    assert out.slices == [
        {
            "merchant_country": "DE",
            "period": "2025-09-01",
            "source": "grp_merchant_monthly",
            "status": "FAIL",
        }
    ]
    assert out.measured["columns"][NAME]["rows_mismatched"] == 1


def test_published_value_where_the_formula_is_undefined_fails() -> None:
    df = _rows([0.003, 0.005, 0.0])
    assert evaluate(Evidence({NAME: df}, INFO), THR).status == "FAIL"


def test_not_applicable_without_precomputed_columns() -> None:
    assert evaluate(Evidence({}, {"applicable": False}), THR).status == "PASS"
