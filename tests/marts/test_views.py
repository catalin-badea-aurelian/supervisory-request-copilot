"""Mart views: generated from the pack, committed unchanged, and equal to copilot results.

The consistency tests run the views on DuckDB (the same BigQuery SQL, transpiled) and
compare them with the copilot's own compiled queries, offline. FR-D6 against the deployed
BigQuery views is in test_consistency_bigquery.py (marker `bigquery`).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import sqlglot

from copilot.marts.deploy import VIEWS_DIR
from copilot.marts.views import (
    NON_ADDITIVE,
    UNIT_MULTIPLIER,
    components,
    generate,
    monthly_metrics,
    render_file,
    substitute,
)
from copilot.packs.compiler import MetricRequest, compile_metric
from tests.marts.local import connect, query_view


def test_committed_view_files_match_the_generator(pack):
    views = generate(pack)
    assert sorted(p.stem for p in VIEWS_DIR.glob("*.sql")) == sorted(views)
    for name, sql in views.items():
        text = (VIEWS_DIR / f"{name}.sql").read_text("utf-8")
        assert text == render_file(pack, name, sql), f"{name}.sql was edited; run make views"
        assert text.startswith(f"-- GENERATED FROM domain pack payments@{pack.pack.version}")


def test_views_parse_and_reference_only_placeholders(pack):
    for name, sql in generate(pack).items():
        sqlglot.parse_one(sql, read="bigquery")
        for ref in sqlglot.parse_one(sql, read="bigquery").find_all(sqlglot.exp.Table):
            if ref.catalog:
                assert ref.catalog == "${project}" and ref.db.startswith("${prefix}_"), (name, ref)


def test_substitute_rejects_bad_identifiers():
    assert substitute("`${project}.${prefix}_raw.t`", "my-proj-1", "spb") == "`my-proj-1.spb_raw.t`"
    for project, prefix in (("", "spb"), ("x; DROP", "spb"), ("my-proj-1", "spb`")):
        with pytest.raises(ValueError):
            substitute("x", project, prefix)


def test_every_glossary_metric_is_served_once(pack):
    comps = components(pack)
    served = {m for m in comps if m in pack.metrics}
    served |= {
        mid
        for mid, m in pack.metrics.items()
        if m.numerator in comps and m.denominator in comps and mid not in NON_ADDITIVE
    }
    served |= set(NON_ADDITIVE) & set(monthly_metrics(pack))
    assert served == {mid for mid, m in pack.metrics.items() if m.status == "approved"}
    for mid in NON_ADDITIVE:
        assert mid in pack.metrics, f"NON_ADDITIVE names an unknown metric {mid}"


def test_marts_read_only_independent_sources(pack):
    for name in ("mart_ewi_components", "mart_ewi_monthly"):
        tree = sqlglot.parse_one(generate(pack)[name], read="bigquery")
        tables = {t.name for t in tree.find_all(sqlglot.exp.Table)}
        assert "grp_merchant_monthly" not in tables


# ---- consistency on DuckDB (offline FR-D6) ---------------------------------------------------
def _copilot(ds, pack, metric_id, start, end, grain, dims) -> pd.DataFrame:
    req = MetricRequest(metric_id, start, end, pack.pack.default_as_of, grain, dims)
    q = compile_metric(pack, req, "independent", "duckdb")
    return ds.backend.query(q.sql, q.params)


def _formula(df: pd.DataFrame, pack, metric_id: str) -> pd.Series:
    m = pack.metrics[metric_id]
    if m.numerator is None:
        return df[metric_id].astype(float)
    num, den = df[m.numerator].astype(float), df[m.denominator].astype(float)
    return (num / den.where(den != 0)) * UNIT_MULTIPLIER[m.unit]


@pytest.fixture(scope="module")
def con(small_default):
    return connect(small_default.parquet_dir)


@pytest.mark.parametrize(
    "grain,dims,start,end",
    [
        ("month", ("period", "merchant_country"), date(2024, 10, 1), date(2025, 9, 30)),
        ("quarter", ("period", "mcc_group"), date(2025, 1, 1), date(2025, 9, 30)),
        ("month", ("period",), date(2025, 6, 1), date(2025, 9, 30)),
    ],
)
def test_components_reaggregate_to_copilot_results(
    con, small_default, pack, grain, dims, start, end
):
    comp = query_view(con, pack, "mart_ewi_components")
    comp["period"] = (
        pd.to_datetime(comp["period_month"])
        .dt.to_period("Q" if grain == "quarter" else "M")
        .dt.start_time.dt.date
    )
    comp = comp[(comp["period"] >= start) & (comp["period"] <= end)]
    keys = list(dims)
    sums = comp.groupby(keys, dropna=False)[list(components(pack))].sum().reset_index()
    checked = 0
    for mid in sorted(pack.metrics):
        if mid in NON_ADDITIVE:
            continue
        ref = _copilot(small_default, pack, mid, start, end, grain, dims)
        ref["period"] = pd.to_datetime(ref["period"]).dt.date
        merged = ref.merge(sums, on=keys, how="outer", suffixes=("_copilot", ""), indicator=True)
        assert (merged["_merge"] == "both").all(), f"{mid}: slices differ"
        mart = _formula(merged, pack, mid)
        copilot = merged[f"{mid}_copilot" if f"{mid}_copilot" in merged else mid].astype(float)
        pd.testing.assert_series_equal(
            mart.reset_index(drop=True),
            copilot.reset_index(drop=True),
            check_names=False,
            rtol=1e-9,
            atol=1e-9,
        )
        checked += 1
    assert checked == len(pack.metrics) - len(NON_ADDITIVE)


def test_monthly_values_rag_and_xx(con, small_default, pack):
    mon = query_view(con, pack, "mart_ewi_monthly")
    assert pack.pack.unknown_country_code not in set(mon["merchant_country"])
    r = pack.pack.data_range
    for mid in monthly_metrics(pack):
        ref = _copilot(
            small_default, pack, mid, r["start"], r["end"], "month", ("period", "merchant_country")
        )
        ref = ref[ref["merchant_country"] != pack.pack.unknown_country_code]
        got = mon[mon["metric_id"] == mid].copy()
        got["period"] = pd.to_datetime(got["period_month"]).dt.date
        ref["period"] = pd.to_datetime(ref["period"]).dt.date
        m = ref.merge(got, on=["period", "merchant_country"], how="outer", indicator=True)
        assert (m["_merge"] == "both").all(), mid
        pd.testing.assert_series_equal(
            m["value"].astype(float), m[mid].astype(float), check_names=False, rtol=1e-9
        )
        ewi = pack.metrics[mid].ewi
        if ewi is None:
            assert set(got["rag_status"]) == {"NOT_ASSESSED"}
            continue
        amber, red = ewi.amber, ewi.red
        expected = got["value"].map(
            lambda v, a=amber, r=red: "RED" if v >= r else "AMBER" if v >= a else "GREEN"
        )
        assert (got["rag_status"] == expected).all(), mid


def test_xx_volume_stays_visible_in_components(con, pack):
    comp = query_view(con, pack, "mart_ewi_components")
    xx = comp[comp["merchant_country"] == pack.pack.unknown_country_code]
    assert not xx.empty and xx["settled_volume_eur"].sum() > 0


def test_timeliness_flags_the_september_drop(con, pack):
    t = query_view(con, pack, "mart_timeliness")
    t["partition_month"] = pd.to_datetime(t["partition_month"]).dt.date
    sep = t[(t["table_name"] == "src_transactions") & (t["partition_month"] == date(2025, 9, 1))]
    assert sep["status"].tolist() == ["INCOMPLETE"]
    assert (t[t["partition_month"] <= date(2025, 6, 1)]["status"] == "COMPLETE").all()
