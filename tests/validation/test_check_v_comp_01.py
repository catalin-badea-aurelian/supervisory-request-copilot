"""V-COMP-01: share of unknown or null dimension values per month."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_comp_01 import evaluate

INFO = {
    "applicable": True,
    "dims": ["merchant_country"],
    "weight": "settled_volume_eur",
    "unknown_country": "XX",
}
THR = {"warn": 0.001, "fail": 0.01}


def _frame(xx_aug: float, xx_sep: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["2025-08-01", "2025-08-01", "2025-09-01", "2025-09-01"],
            "merchant_country": ["DE", "XX", "DE", "XX"],
            "settled_volume_eur": [1000.0 - xx_aug, xx_aug, 1000.0 - xx_sep, xx_sep],
        }
    )


def test_complete_dimensions_pass() -> None:
    assert evaluate(Evidence({"result": _frame(0.0, 0.0)}, INFO), THR).status == "PASS"


def test_share_between_thresholds_warns_and_above_fails() -> None:
    out = evaluate(Evidence({"result": _frame(5.0, 20.0)}, INFO), THR)  # 0.5% and 2%
    assert out.status == "FAIL"
    by_period = {s["period"]: s["status"] for s in out.slices}
    assert by_period == {"2025-08-01": "WARN", "2025-09-01": "FAIL"}


def test_null_country_counts_as_unknown() -> None:
    df = _frame(0.0, 0.0)
    df.loc[3, "merchant_country"] = None
    df.loc[3, "settled_volume_eur"] = 50.0
    out = evaluate(Evidence({"result": df}, INFO), THR)
    assert out.status == "FAIL"


def test_null_mcc_counts_as_unknown() -> None:
    df = pd.DataFrame(
        {
            "period": ["2025-09-01", "2025-09-01"],
            "mcc_group": ["travel", None],
            "settled_volume_eur": [90.0, 10.0],
        }
    )
    info = {**INFO, "dims": ["mcc_group"]}
    assert evaluate(Evidence({"result": df}, info), THR).status == "FAIL"


def test_not_applicable_without_categorical_dimension() -> None:
    out = evaluate(Evidence({}, {"applicable": False}), THR)
    assert out.status == "PASS" and out.measured["applicable"] is False
