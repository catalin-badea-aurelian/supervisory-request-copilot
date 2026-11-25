"""V-RERUN-01 (determinism) and V-SQL-01 (query policy)."""

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_rerun_01 import evaluate as rerun
from copilot.validation.checks.v_sql_01 import evaluate as sql_policy

A = pd.DataFrame({"merchant_country": ["DE", "FR"], "v": [1.0, 2.0]})


def test_identical_rerun_passes_regardless_of_row_order() -> None:
    shuffled = A.iloc[::-1].reset_index(drop=True)
    assert rerun(Evidence({"primary": A, "rerun": shuffled}), {}).status == "PASS"


def test_different_rerun_fails() -> None:
    other = A.assign(v=[1.0, 2.5])
    out = rerun(Evidence({"primary": A, "rerun": other}), {})
    assert out.status == "FAIL"
    assert out.measured["result_sha256"] != out.measured["rerun_sha256"]


def test_template_query_passes_and_non_template_warns() -> None:
    ok = sql_policy(Evidence(info={"non_template": False, "template_id": "m@1:preferred"}), {})
    assert ok.status == "PASS"
    flagged = sql_policy(Evidence(info={"non_template": True, "template_id": None}), {})
    assert flagged.status == "WARN"
