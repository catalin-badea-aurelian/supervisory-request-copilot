"""Canonical hashing is order-independent, type-normalised and sensitive to content."""

from decimal import Decimal

import pandas as pd

from copilot.data.hashing import canonical_hash


def test_row_order_does_not_matter() -> None:
    a = pd.DataFrame({"k": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]})
    b = a.iloc[::-1].reset_index(drop=True)
    assert canonical_hash(a) == canonical_hash(b)


def test_float_noise_below_precision_is_ignored() -> None:
    a = pd.DataFrame({"v": [0.1 + 0.2]})
    b = pd.DataFrame({"v": [0.3]})
    assert canonical_hash(a) == canonical_hash(b)


def test_decimal_and_float_hash_equal() -> None:
    a = pd.DataFrame({"v": [Decimal("12.30"), Decimal("4.00")]})
    b = pd.DataFrame({"v": [12.3, 4.0]})
    assert canonical_hash(a) == canonical_hash(b)


def test_content_change_changes_hash() -> None:
    a = pd.DataFrame({"k": ["a"], "v": [1.0]})
    b = pd.DataFrame({"k": ["a"], "v": [1.0000001]})
    assert canonical_hash(a) != canonical_hash(b)


def test_nulls_sort_deterministically() -> None:
    a = pd.DataFrame({"k": ["a", None, "b"], "v": [1, 2, 3]})
    b = a.iloc[[2, 0, 1]].reset_index(drop=True)
    assert canonical_hash(a) == canonical_hash(b)
