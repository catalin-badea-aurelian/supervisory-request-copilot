"""V-COMP-02: every in-scope country x period is present, or explicitly zero."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_comp_02 import evaluate

INFO = {
    "axes": {"period": ["2025-07-01", "2025-08-01"], "merchant_country": ["DE", "FR"]},
    "dims": ["period", "merchant_country"],
    "unknown_country": "XX",
}


def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["period", "merchant_country", "settled_volume_eur"])


def test_full_perimeter_passes_including_explicit_zero() -> None:
    df = _frame(
        [
            ("2025-07-01", "DE", 1.0),
            ("2025-07-01", "FR", 0.0),
            ("2025-08-01", "DE", 1.0),
            ("2025-08-01", "FR", 2.0),
        ]
    )
    assert evaluate(Evidence({"result": df}, INFO), {}).status == "PASS"


def test_missing_combination_fails() -> None:
    df = _frame([("2025-07-01", "DE", 1.0), ("2025-07-01", "FR", 1.0), ("2025-08-01", "DE", 1.0)])
    out = evaluate(Evidence({"result": df}, INFO), {})
    assert out.status == "FAIL"
    assert out.slices == [{"merchant_country": "FR", "period": "2025-08-01", "status": "FAIL"}]


def test_no_dimensions_needs_one_row() -> None:
    info = {"axes": {}, "dims": [], "unknown_country": "XX"}
    empty = pd.DataFrame({"settled_volume_eur": []})
    assert evaluate(Evidence({"result": empty}, info), {}).status == "FAIL"
    one = pd.DataFrame({"settled_volume_eur": [1.0]})
    assert evaluate(Evidence({"result": one}, info), {}).status == "PASS"
