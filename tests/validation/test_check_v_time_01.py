"""V-TIME-01: load completeness per source and month from the load log."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_time_01 import evaluate

INFO = {"sources": ["src_merchants", "src_transactions"], "months": ["2025-08", "2025-09"]}
THR = {"warn": 0.99, "fail": 0.95}


def _log(sep_rows: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "table_name": ["src_transactions", "src_transactions"],
            "partition_month": ["2025-08", "2025-09"],
            "row_count": [1000, sep_rows],
            "expected_row_count": [1000, 1000],
        }
    )


def test_complete_load_passes_and_reports_untracked_sources() -> None:
    out = evaluate(Evidence({"log": _log(1000)}, INFO), THR)
    assert out.status == "PASS"
    assert out.measured["not_tracked"] == ["src_merchants"]


def test_slightly_incomplete_load_warns() -> None:
    out = evaluate(Evidence({"log": _log(970)}, INFO), THR)
    assert out.status == "WARN"


def test_partial_load_fails_for_that_month() -> None:
    out = evaluate(Evidence({"log": _log(600)}, INFO), THR)
    assert out.status == "FAIL"
    assert out.slices == [{"period": "2025-09-01", "source": "src_transactions", "status": "FAIL"}]


def test_over_count_is_not_flagged() -> None:
    assert evaluate(Evidence({"log": _log(1200)}, INFO), THR).status == "PASS"


def test_missing_month_in_the_log_fails() -> None:
    log = _log(1000).iloc[[0]]
    out = evaluate(Evidence({"log": log}, INFO), THR)
    assert out.status == "FAIL" and out.measured["missing_months"]
