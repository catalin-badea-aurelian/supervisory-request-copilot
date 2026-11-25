"""V-PK-01: duplicate keys in any source the metric reads."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_pk_01 import evaluate

INFO = {"undeclared": [], "unknown_country": "XX"}


def _none() -> pd.DataFrame:
    return pd.DataFrame({"duplicate_keys": [0], "duplicate_rows": [None]})


def test_no_duplicates_pass() -> None:
    frames = {"src_transactions": _none(), "src_merchants": _none()}
    assert evaluate(Evidence(frames, INFO), {}).status == "PASS"


def test_duplicated_group_rows_fail_with_source_period_and_country() -> None:
    dup = pd.DataFrame(
        {
            "period": [pd.Timestamp("2025-08-01")],
            "merchant_country": ["ES"],
            "duplicate_keys": [10],
            "duplicate_rows": [20],
        }
    )
    out = evaluate(Evidence({"grp_merchant_monthly": dup, "src_merchants": _none()}, INFO), {})
    assert out.status == "FAIL"
    assert out.slices == [
        {
            "source": "grp_merchant_monthly",
            "period": "2025-08-01",
            "merchant_country": "ES",
            "status": "FAIL",
        }
    ]
    assert out.measured["duplicate_keys"] == {"grp_merchant_monthly": 10, "src_merchants": 0}


def test_empty_grouped_result_means_no_duplicates() -> None:
    empty = pd.DataFrame(columns=["period", "duplicate_keys", "duplicate_rows"])
    assert evaluate(Evidence({"src_transactions": empty}, INFO), {}).status == "PASS"


def test_a_source_without_declared_key_is_a_warning() -> None:
    out = evaluate(Evidence({}, {**INFO, "undeclared": ["some_table"]}), {})
    assert out.status == "WARN"
