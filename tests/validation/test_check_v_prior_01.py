"""V-PRIOR-01: overlapping periods match prior submissions (WARN only)."""

from datetime import date

import pandas as pd

from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_prior_01 import _quarters_inside, evaluate

INFO = {"applicable": True, "value": "settled_volume_eur", "unknown_country": "XX"}
THR = {"relative": 0.001}


def _ev(recomputed: float) -> Evidence:
    prior = pd.DataFrame({"period": ["2025-Q1"], "merchant_country": ["DE"], "value": [1000.0]})
    rec = pd.DataFrame(
        {
            "period": [date(2025, 1, 1)],
            "merchant_country": ["DE"],
            "settled_volume_eur": [recomputed],
        }
    )
    return Evidence({"prior": prior, "recomputed": rec}, INFO)


def test_matching_submission_passes() -> None:
    assert evaluate(_ev(1000.0), THR).status == "PASS"


def test_difference_warns() -> None:
    out = evaluate(_ev(1100.0), THR)
    assert out.status == "WARN"
    assert out.slices == [{"merchant_country": "DE", "period": "2025-Q1", "status": "WARN"}]


def test_not_applicable_passes_with_reason() -> None:
    out = evaluate(Evidence({}, {"applicable": False, "reason": "MCC filter"}), THR)
    assert out.status == "PASS" and out.measured["reason"] == "MCC filter"


def test_only_full_quarters_inside_the_period_are_compared() -> None:
    assert _quarters_inside(date(2025, 1, 1), date(2025, 6, 30)) == [
        date(2025, 1, 1),
        date(2025, 4, 1),
    ]
    assert _quarters_inside(date(2025, 2, 1), date(2025, 6, 30)) == [date(2025, 4, 1)]
    assert _quarters_inside(date(2025, 7, 1), date(2025, 8, 31)) == []
