"""V-MAT-01: immature months WARN; the check can never FAIL."""

from datetime import date, timedelta

from copilot.validation.checks import REGISTRY
from copilot.validation.checks.base import Evidence
from copilot.validation.checks.v_mat_01 import evaluate
from copilot.validation.frames import month_starts

MONTHS = [m.isoformat() for m in month_starts(date(2025, 6, 1), date(2025, 9, 30))]


def _ev(as_of: date, days: int | None = 60) -> Evidence:
    return Evidence(info={"months": MONTHS, "as_of": as_of.isoformat(), "maturity_days": days})


def test_months_inside_the_window_warn() -> None:
    out = evaluate(_ev(date(2025, 10, 15)), {})
    assert out.status == "WARN"
    assert [s["period"] for s in out.slices] == ["2025-08-01", "2025-09-01"]


def test_mature_months_pass() -> None:
    assert evaluate(_ev(date(2025, 12, 1)), {}).status == "PASS"


def test_never_fails_for_any_as_of_date() -> None:
    d = date(2025, 5, 1)
    while d < date(2026, 2, 1):
        assert evaluate(_ev(d), {}).status in {"PASS", "WARN"}
        d += timedelta(days=7)
    assert REGISTRY["V-MAT-01"].max_status == "WARN"


def test_not_applicable_without_maturity_window() -> None:
    assert evaluate(_ev(date(2025, 10, 15), None), {}).status == "PASS"
