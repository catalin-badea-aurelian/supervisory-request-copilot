"""Golden tests: every governed template equals the independent pandas reference (DuckDB)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from copilot.packs.compiler import MetricRequest, compile_metric
from tests.reference.metrics import reference_metric

AS_OF = date(2025, 10, 15)
ALL_DIMS = ("period", "merchant_country", "mcc_group")

SHAPES = {
    "q1q2_country_mcc": dict(
        period_start=date(2025, 1, 1), period_end=date(2025, 6, 30), grain="quarter", dims=ALL_DIMS
    ),
    "monthly_filtered": dict(
        period_start=date(2024, 10, 1),
        period_end=date(2025, 7, 31),
        grain="month",
        dims=("period", "merchant_country"),
        filters={"merchant_country": ("DE", "FR")},
    ),
    "single_quarter_no_dims": dict(
        period_start=date(2024, 10, 1), period_end=date(2024, 12, 31), grain="quarter", dims=()
    ),
    "mcc_slice": dict(
        period_start=date(2024, 10, 1),
        period_end=date(2025, 6, 30),
        grain="quarter",
        dims=("mcc_group",),
        filters={"mcc_group": ("travel", "electronics")},
    ),
}
RAW_METHOD_METRICS = {"fraud_loss_rate_bps", "refund_rate_value"}


def _normalise(df: pd.DataFrame, dims: tuple[str, ...]) -> pd.DataFrame:
    df = df.copy()
    if "period" in df:
        df["period"] = pd.to_datetime(df["period"]).dt.date
    keys = list(dims)
    for col in df.columns:
        if col not in keys:
            df[col] = df[col].astype("float64")
    return df.sort_values(keys).reset_index(drop=True) if keys else df.reset_index(drop=True)


def _run(ds, pack, metric_id, path, shape, filters=None):
    shape = dict(shape)
    req = MetricRequest(
        metric_id=metric_id,
        as_of=AS_OF,
        period_start=shape["period_start"],
        period_end=shape["period_end"],
        grain=shape["grain"],
        dims=tuple(shape["dims"]),
        filters=shape.get("filters", {}),
    )
    q = compile_metric(pack, req, path, "duckdb")
    actual = _normalise(ds.backend.query(q.sql, q.params), req.dims)
    expected = _normalise(
        reference_metric(
            ds.frames,
            metric_id,
            req.period_start,
            req.period_end,
            AS_OF,
            req.grain,
            req.dims,
            req.filters,
        ),
        req.dims,
    )
    return actual, expected, req.dims


def _assert_equal(actual: pd.DataFrame, expected: pd.DataFrame, dims) -> None:
    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected) > 0
    for d in dims:
        assert actual[d].tolist() == expected[d].tolist()
    for col in actual.columns:
        if col not in dims:
            np.testing.assert_allclose(
                actual[col].to_numpy(),
                expected[col].to_numpy(),
                rtol=1e-9,
                atol=1e-9,
                equal_nan=True,
            )


METRICS = [
    "settled_volume_eur",
    "transaction_count",
    "avg_ticket_eur",
    "chargeback_amount_eur",
    "chargeback_rate_value",
    "chargeback_rate_count",
    "fraud_loss_rate_bps",
    "refund_rate_value",
    "active_merchants",
    "top10_merchant_concentration",
]


@pytest.mark.parametrize("path", ["preferred", "independent"])
@pytest.mark.parametrize("shape_name", list(SHAPES))
@pytest.mark.parametrize("metric_id", METRICS)
def test_template_equals_reference_on_clean_data(small_clean, pack, metric_id, path, shape_name):
    actual, expected, dims = _run(small_clean, pack, metric_id, path, SHAPES[shape_name])
    _assert_equal(actual, expected, dims)


@pytest.mark.parametrize("path", ["preferred", "independent"])
@pytest.mark.parametrize("shape_name", ["q1q2_country_mcc", "mcc_slice"])
@pytest.mark.parametrize("metric_id", METRICS)
def test_requests_up_to_july_are_unaffected_by_planted_defects(
    small_default, pack, metric_id, path, shape_name
):
    # defects only touch Aug-Sep 2025, so S1 / S3 style requests run on clean data
    actual, expected, dims = _run(small_default, pack, metric_id, path, SHAPES[shape_name])
    _assert_equal(actual, expected, dims)


@pytest.mark.parametrize("path", ["preferred", "independent"])
@pytest.mark.parametrize("metric_id", sorted(RAW_METHOD_METRICS))
def test_payment_method_dimension_on_raw_metrics(small_clean, pack, metric_id, path):
    shape = dict(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 3, 31),
        grain="quarter",
        dims=("period", "payment_method"),
    )
    actual, expected, dims = _run(small_clean, pack, metric_id, path, shape)
    _assert_equal(actual, expected, dims)


@pytest.mark.parametrize("metric_id", METRICS)
def test_preferred_and_independent_paths_agree_on_clean_data(small_clean, pack, metric_id):
    shape = SHAPES["q1q2_country_mcc"]
    a, _, dims = _run(small_clean, pack, metric_id, "preferred", shape)
    b, _, _ = _run(small_clean, pack, metric_id, "independent", shape)
    _assert_equal(a, b, dims)


def test_monthly_series_is_reproducible_through_hashing(small_clean, pack):
    from copilot.data.hashing import canonical_hash

    req = MetricRequest(
        "chargeback_rate_value",
        date(2024, 10, 1),
        date(2025, 7, 31),
        AS_OF,
        "month",
        ("period", "merchant_country"),
    )
    q = compile_metric(pack, req, "preferred", "duckdb")
    h1 = canonical_hash(small_clean.backend.query(q.sql, q.params))
    h2 = canonical_hash(small_clean.backend.query(q.sql, q.params))
    assert h1 == h2
