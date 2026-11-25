"""Planted defects D1-D4 are present exactly as tests/fixtures/defects_expected.yaml says."""

from datetime import date

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from copilot.data.hashing import canonical_hash
from copilot.packs.compiler import MetricRequest, compile_metric
from tests.reference.metrics import reference_metric

AS_OF = date(2025, 10, 15)
KEY = ["month", "merchant_country", "mcc_group"]


def _t(ds, name: str) -> pd.DataFrame:
    return pq.read_table(ds.parquet_dir / f"{name}.parquet").to_pandas()


def _group(ds) -> pd.DataFrame:
    g = _t(ds, "grp_merchant_monthly")
    g["month"] = pd.to_datetime(g["month"])
    for c in ("settled_volume_eur", "cb_amount_eur"):
        g[c] = g[c].astype("float64")
    return g


def _xx_rows(g: pd.DataFrame) -> pd.DataFrame:
    return g[g["merchant_country"] == "XX"]


# ---- D1 ---------------------------------------------------------------------------------
def test_d1_duplicate_group_rows_exactly_in_es_august(small_default, small_clean, expected_defects):
    sl = expected_defects["defects"]["D1"]["slice"]
    dflt, clean = _group(small_default), _group(small_clean)
    assert not clean.duplicated(KEY).any()
    dup = dflt[dflt.duplicated(KEY, keep=False)]
    assert len(dup) > 0 and len(dup) % 2 == 0
    assert set(dup["month"]) == {pd.Timestamp(sl["month"])}
    assert set(dup["merchant_country"]) == {sl["merchant_country"]}


def test_d1_is_visible_against_the_reference_only_for_es_august(small_default, pack):
    req = MetricRequest(
        "settled_volume_eur",
        date(2025, 8, 1),
        date(2025, 8, 31),
        AS_OF,
        "month",
        ("period", "merchant_country"),
    )
    q = compile_metric(pack, req, "preferred", "duckdb")
    got = small_default.backend.query(q.sql, q.params).set_index("merchant_country")
    ref = reference_metric(
        small_default.frames,
        "settled_volume_eur",
        req.period_start,
        req.period_end,
        AS_OF,
        "month",
        req.dims,
    ).set_index("merchant_country")
    ratio = (got["settled_volume_eur"].astype(float) / ref["settled_volume_eur"]).round(6)
    assert ratio["ES"] == 2.0
    assert (ratio.drop("ES") == 1.0).all()


# ---- D2 ---------------------------------------------------------------------------------
def test_d2_missing_country_for_a_small_share_of_september_onboardings(
    small_default, small_clean, expected_defects
):
    spec = expected_defects["defects"]["D2"]
    m = _t(small_default, "src_merchants")
    nulls = m[m["merchant_country"].isna()]
    assert len(nulls) > 0
    onboarded = pd.to_datetime(nulls["onboarding_date"]).dt.to_period("M").astype(str)
    assert onboarded.eq("2025-09").all()
    share = len(nulls) / len(m)
    bounds = spec["null_merchants_share_of_all"]
    assert bounds["min"] <= share <= bounds["max"]
    assert _t(small_clean, "src_merchants")["merchant_country"].notna().all()


def test_d2_propagates_as_xx_to_the_group_product_in_september_only(small_default, small_clean):
    xx = _xx_rows(_group(small_default))
    assert len(xx) > 0 and set(xx["month"]) == {pd.Timestamp("2025-09-01")}
    assert _xx_rows(_group(small_clean)).empty


# ---- D3 ---------------------------------------------------------------------------------
def test_d3_only_part_of_september_is_loaded_at_the_as_of_date(
    small_default, small_clean, expected_defects
):
    spec = expected_defects["defects"]["D3"]["loaded_share_at_as_of"]
    cutoff = pd.Timestamp(AS_OF, tz="UTC") + pd.Timedelta(days=1)

    def loaded_share(ds) -> float:
        t = _t(ds, "src_transactions")
        sept = t[t["txn_ts"].dt.strftime("%Y-%m") == "2025-09"]
        return float((sept["_loaded_at"] < cutoff).mean())

    assert spec["min"] <= loaded_share(small_default) <= spec["max"]
    assert loaded_share(small_clean) == 1.0


def test_d3_is_recorded_in_the_load_log_for_september_only(
    small_default, small_clean, expected_defects
):
    spec = expected_defects["defects"]["D3"]["loaded_share_at_as_of"]
    for ds, partial in ((small_default, True), (small_clean, False)):
        log = _t(ds, "meta_load_log")
        tx = log[log["table_name"] == "src_transactions"].set_index("partition")
        ratio = tx["row_count"] / tx["expected_row_count"]
        if partial:
            assert spec["min"] <= ratio["2025-09"] <= spec["max"]
            assert (ratio.drop("2025-09") == 1.0).all()
        else:
            assert (ratio == 1.0).all()


def test_d3_group_product_was_built_on_the_same_partial_load(small_default, pack):
    req = MetricRequest(
        "transaction_count", date(2025, 9, 1), date(2025, 9, 30), AS_OF, "month", ("period",)
    )
    pref = compile_metric(pack, req, "preferred", "duckdb")
    indep = compile_metric(pack, req, "independent", "duckdb")
    a = small_default.backend.query(pref.sql, pref.params)["transaction_count"].iloc[0]
    b = small_default.backend.query(indep.sql, indep.params)["transaction_count"].iloc[0]
    assert a == b  # partial, but identical: the raw path sees the same loaded rows


# ---- D4 ---------------------------------------------------------------------------------
def test_d4_cb_rate_switches_to_count_basis_from_september(small_default, small_clean):
    g = _group(small_default)
    g = g[g["settled_volume_eur"] > 0]
    value_rate = g["cb_amount_eur"] / g["settled_volume_eur"]
    count_rate = g["cb_count"] / g["txn_count"]
    before = g["month"] < pd.Timestamp("2025-09-01")
    np.testing.assert_allclose(g.loc[before, "cb_rate"], value_rate[before], rtol=1e-12)
    np.testing.assert_allclose(g.loc[~before, "cb_rate"], count_rate[~before], rtol=1e-12)
    assert (np.abs(count_rate[~before] - value_rate[~before]) > 1e-6).any()  # a real difference
    gc = _group(small_clean)
    gc = gc[gc["settled_volume_eur"] > 0]
    np.testing.assert_allclose(
        gc["cb_rate"], gc["cb_amount_eur"] / gc["settled_volume_eur"], rtol=1e-12
    )


# ---- containment and the clean flavour ---------------------------------------------------
def test_defects_are_confined_to_august_and_september_2025(small_default, small_clean):
    for name in (
        "src_chargebacks",
        "src_fraud_losses",
        "ledger_control_totals",
        "prior_submissions",
    ):
        assert canonical_hash(_t(small_default, name)) == canonical_hash(
            _t(small_clean, name)
        ), name
    gd, gc = _group(small_default), _group(small_clean)
    cut = pd.Timestamp("2025-08-01")
    cols = [c for c in gd.columns if c != "_loaded_at"]
    assert canonical_hash(gd.loc[gd["month"] < cut, cols]) == canonical_hash(
        gc.loc[gc["month"] < cut, cols]
    )
    td, tc = _t(small_default, "src_transactions"), _t(small_clean, "src_transactions")
    before = td["txn_ts"] < pd.Timestamp("2025-08-01", tz="UTC")
    assert canonical_hash(td[before]) == canonical_hash(tc[before])
    assert td["txn_id"].tolist() == tc["txn_id"].tolist()


def test_clean_flavour_has_no_defect_signatures(small_clean):
    g = _group(small_clean)
    assert not g.duplicated(KEY).any()
    assert _xx_rows(g).empty
    log = _t(small_clean, "meta_load_log")
    grp = log[log["table_name"] == "grp_merchant_monthly"]
    assert (grp["row_count"] == grp["expected_row_count"]).all()


def test_manifest_does_not_reveal_which_defects_were_planted(small_default):
    text = str(small_default.manifest).lower()
    assert "defect" not in text
