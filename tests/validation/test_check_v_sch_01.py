"""V-SCH-01: result columns, types and grain."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_sch_01 import evaluate

INFO = {
    "dims": ["period", "merchant_country"],
    "value_columns": ["chargeback_amount_eur", "settled_volume_eur", "chargeback_rate_value"],
    "cardinality": {"period": 2, "merchant_country": 9},
}


def _clean() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["2025-07-01", "2025-07-01", "2025-08-01"],
            "merchant_country": ["DE", "FR", "DE"],
            "chargeback_amount_eur": [1.0, 2.0, 3.0],
            "settled_volume_eur": [100.0, 200.0, 300.0],
            "chargeback_rate_value": [0.01, 0.01, 0.01],
        }
    )


def test_conforming_result_passes() -> None:
    assert evaluate(Evidence({"result": _clean()}, INFO), {}).status == "PASS"


def test_duplicate_keys_fail() -> None:
    df = pd.concat([_clean(), _clean().iloc[[0]]])
    out = evaluate(Evidence({"result": df}, INFO), {})
    assert out.status == "FAIL"
    assert {s["line"] for s in out.slices} == {"duplicate_keys"}


def test_missing_and_unexpected_columns_fail() -> None:
    df = _clean().drop(columns=["settled_volume_eur"]).assign(extra=1)
    out = evaluate(Evidence({"result": df}, INFO), {})
    assert {s["line"] for s in out.slices} == {"missing_columns", "unexpected_columns"}


def test_non_numeric_values_fail() -> None:
    df = _clean().astype({"chargeback_rate_value": object})
    df.loc[0, "chargeback_rate_value"] = "n/a"
    out = evaluate(Evidence({"result": df}, INFO), {})
    assert [s["line"] for s in out.slices] == ["non_numeric_columns"]


def test_fan_out_beyond_the_dimension_cardinality_fails() -> None:
    info = {**INFO, "cardinality": {"period": 1, "merchant_country": 2}}
    out = evaluate(Evidence({"result": _clean()}, info), {})
    assert [s["line"] for s in out.slices] == ["fan_out"]
