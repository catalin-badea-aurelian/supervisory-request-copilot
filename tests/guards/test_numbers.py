"""Number guard: valid reformatting passes, invented or derived numbers are caught."""

from __future__ import annotations

import pytest

from copilot.guards.numbers import guard_message, invented_numbers

DATA = {
    "check": {
        "check_id": "V-REC-01",
        "measured": {"rel_diff": 0.1512151921, "result": 1793696.26, "ledger": 896848.13},
        "affected_slices": [{"merchant_country": "ES", "period": "2025-08-01"}],
        "rows_compared": 16,
    },
    "derived": {
        "observed": "1,793,696.26",
        "expected": "896,848.13",
        "difference": "+896,848.13",
        "ratio": "2.00×",
        "relative_difference": "15.1%",
        "share_of_total_gap": "100.0%",
        "duplicate_keys": {"grp_merchant_monthly": "10"},
    },
}


@pytest.mark.parametrize(
    "text",
    [
        "Settled volume 1,793,696.26 against 896,848.13.",
        "Settled volume 1793696.26 against 896848.13.",  # separators dropped
        "Settled volume 1 793 696.26 against 896 848.13.",  # (narrow) no-break spaces
        "ES is 2.00× the ledger.",
        "ES is 2.00x the ledger.",  # x for ×
        "ES is 2× the ledger, 2.0x in short.",  # trailing zeros
        "ES is 2.00 × the ledger.",  # space before the suffix
        "ES is 2.00 times the ledger.",  # the word for ×
        "A gap of 15.1% overall; ES explains 100.0% (that is 100%) of it.",
        "Over by +896,848.13, i.e. 896,848.13; the gap is −896,848.13 from the ledger side.",
        "10 duplicate keys; 16 rows compared.",
        "Raw value 0.1512151921 as measured.",
        "Check V-REC-01 failed for ES in 2025-08-01, i.e. August 2025 (period 2025-08, Q3).",
        "Also see V-PK-01 and V-DUAL-01 on grp_merchant_monthly.",
        "No numbers at all.",
    ],
)
def test_valid_reformatting_passes(text):
    assert invented_numbers(text, DATA) == []


@pytest.mark.parametrize(
    "text,bad",
    [
        ("Volume is 1,793,696.27 against the ledger.", ["1,793,696.27"]),  # changed digit
        ("A gap of about 15%.", ["15%"]),  # rounded percentage
        ("A relative difference of 0.15.", ["0.15"]),  # rounded fraction
        ("The relative difference is 0.1512151921%.", ["0.1512151921%"]),  # % not supplied
        ("Raw rel_diff as 15.12151921%.", ["15.12151921%"]),  # model's own conversion
        ("ES is 2.1× the ledger.", ["2.1×"]),  # invented ratio
        ("Volume grew 10x.", ["10x"]),  # 10 exists only as a plain count, not as a ratio
        ("ES is 2.00 times, i.e. 200% of the ledger.", ["200%"]),  # derived percentage
        ("Roughly 1.79 million EUR.", ["1.79"]),
        ("Duplicates since 2024.", ["2024"]),  # year not in any input period
        ("Check V-REC-01 shows 17 rows.", ["17"]),  # digits beside a check id still checked
        ("Expected 900,000.00 instead.", ["900,000.00"]),
    ],
)
def test_invented_numbers_are_caught(text, bad):
    assert invented_numbers(text, DATA) == bad


def test_percent_form_is_allowed_only_when_code_supplied_it():
    assert invented_numbers("ES explains 100.0% of the gap.", {"share": "100.0%"}) == []
    assert invented_numbers("ES explains 100.0% of the gap.", {"share": 1.0}) == ["100.0%"]
    assert invented_numbers("ES explains 100.0% of the gap.", {"share": "100.0"}) == ["100.0%"]


def test_ratio_needs_a_ratio_in_the_input():
    assert invented_numbers("2.00×", {"ratio": "2.00×"}) == []
    assert invented_numbers("2.00×", {"value": 2}) == ["2.00×"]


def test_checks_every_field_of_a_structured_output():
    out = {"summary": "1,793,696.26 vs 896,848.13", "impact": "about 15%", "cause": "x"}
    assert invented_numbers(out, DATA) == ["15%"]


def test_message_names_the_numbers_and_the_rule():
    msg = guard_message(["15%", "1.79"])
    assert "15%, 1.79" in msg and "do not compute" in msg
