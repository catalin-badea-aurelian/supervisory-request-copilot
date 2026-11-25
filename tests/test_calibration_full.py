"""Full-scale story checks: the numbers make the demo work (slow: generates 1.5M transactions).

Baseline early-warning indicators stay green except the planted risk event R1; September 2025
shows a ~40% volume drop from the incomplete load; D2 is big enough for V-COMP-01 to fail.
"""

from datetime import date

import pandas as pd
import pytest

from copilot.packs.models import EwiDef
from data.generator.build import build_dataset
from tests.reference.metrics import reference_metric

pytestmark = pytest.mark.slow

AS_OF = date(2025, 10, 15)
START, END = date(2024, 10, 1), date(2025, 9, 30)
COUNTRIES = ["DE", "FR", "ES", "IT", "NL", "BE", "LU", "IE"]
EWI_COLUMN = {
    "chargeback_rate_value": "chargeback_rate_value",
    "fraud_loss_rate_bps": "fraud_loss_rate_bps",
    "refund_rate_value": "refund_rate_value",
    "top10_merchant_concentration": "top10_merchant_concentration",
}


def _monthly(ds, metric_id: str) -> pd.DataFrame:
    df = reference_metric(
        ds.frames, metric_id, START, END, AS_OF, "month", ("period", "merchant_country")
    )
    return df[df["merchant_country"].isin(COUNTRIES)]


def test_volumes_match_the_specification(full_clean) -> None:
    t = full_clean.frames.txns
    assert abs(len(t) - 1_500_000) / 1_500_000 < 0.01
    assert full_clean.frames.merchants["merchant_id"].nunique() == 2000
    assert set(full_clean.frames.merchants["merchant_country"]) == set(COUNTRIES)
    assert full_clean.frames.merchants["mcc_group"].nunique() == 10
    assert t["txn_ts"].min() >= pd.Timestamp("2024-10-01", tz="UTC")
    assert t["txn_ts"].max() < pd.Timestamp("2025-10-01", tz="UTC")
    assert set(t["payment_method"]) == {"card", "wallet"}


@pytest.mark.parametrize("metric_id", list(EWI_COLUMN))
def test_baseline_indicators_are_green_except_the_planted_risk_event(
    full_clean, pack, metric_id
) -> None:
    ewi: EwiDef = pack.metrics[metric_id].ewi
    df = _monthly(full_clean, metric_id)
    col = EWI_COLUMN[metric_id]
    if metric_id == "chargeback_rate_value":
        r1 = (df["merchant_country"] == "IT") & (df["period"] == date(2025, 6, 1))
        assert df.loc[r1, col].iloc[0] > ewi.red  # R1 is red
        df = df[~r1]
    assert (df[col] < ewi.amber).all(), df.loc[df[col] >= ewi.amber]


def test_r1_is_isolated_to_italy_in_june_and_driven_by_travel(full_clean, pack) -> None:
    ewi = pack.metrics["chargeback_rate_value"].ewi
    df = _monthly(full_clean, "chargeback_rate_value")
    june = df[df["period"] == date(2025, 6, 1)].set_index("merchant_country")[
        "chargeback_rate_value"
    ]
    assert june["IT"] > ewi.red
    assert (june.drop("IT") < ewi.amber).all()
    it = df[df["merchant_country"] == "IT"].set_index("period")["chargeback_rate_value"]
    assert (it.drop(date(2025, 6, 1)) < ewi.amber).all()
    # travel is the driver
    by_mcc = reference_metric(
        full_clean.frames,
        "chargeback_amount_eur",
        date(2025, 6, 1),
        date(2025, 6, 30),
        AS_OF,
        "month",
        ("mcc_group",),
        {"merchant_country": ("IT",)},
    ).set_index("mcc_group")["chargeback_amount_eur"]
    assert by_mcc["travel"] / by_mcc.sum() > 0.5


def test_september_volume_drop_is_a_data_event(full_default) -> None:
    def volume(m_start: date, m_end: date) -> float:
        df = reference_metric(
            full_default.frames, "settled_volume_eur", m_start, m_end, AS_OF, "month", ("period",)
        )
        return float(df["settled_volume_eur"].iloc[0])

    aug = volume(date(2025, 8, 1), date(2025, 8, 31))
    sep = volume(date(2025, 9, 1), date(2025, 9, 30))
    assert 0.35 <= 1 - sep / aug <= 0.55


def test_d2_is_large_enough_for_the_completeness_check(full_default, expected_defects) -> None:
    spec = expected_defects["defects"]["D2"]
    df = reference_metric(
        full_default.frames,
        "settled_volume_eur",
        date(2025, 9, 1),
        date(2025, 9, 30),
        AS_OF,
        "month",
        ("period", "merchant_country"),
    ).set_index("merchant_country")["settled_volume_eur"]
    share = df["XX"] / df.sum()
    assert share >= spec["xx_share_of_loaded_september_volume"]["min"]
    m = full_default.frames.merchants
    assert 0.015 <= m["merchant_country"].isna().mean() <= 0.025


def test_d2_alone_is_fully_explained_by_the_unallocated_xx_volume() -> None:
    """With D2 only, the group product still reconciles to the ledger in total.

    Per-country volumes fall short by exactly the volume parked under 'XX'. Whether M2's
    V-REC-01 reports XX as an explicit "unallocated" line is a design choice for M2; this test
    proves the data supports it (nothing is lost, it is only mis-allocated).
    """
    frames = build_dataset(42, 1.0, ("D2",))
    g = frames["grp_merchant_monthly"]
    sep = g[g["month"] == pd.Timestamp("2025-09-01")]
    by_country = sep.groupby("merchant_country")["settled_cents"].sum()
    led = frames["ledger_control_totals"]
    sel = (led["period_month"] == pd.Timestamp("2025-09-01")) & (
        led["measure"] == "settled_volume_eur"
    )
    ledger = led[sel].set_index("merchant_country")["cents"]
    assert by_country.sum() == ledger.sum()
    shortfall = (ledger - by_country.reindex(COUNTRIES)).sum()
    assert shortfall == by_country["XX"] > 0
