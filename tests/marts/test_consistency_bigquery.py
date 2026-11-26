"""FR-D6 on BigQuery: deployed mart views equal copilot-compiled results on sampled slices.

Opt-in (`-m bigquery`): needs the showcase project, `make views` deployed and ADC that can
impersonate spb-copilot. Mart queries run as the copilot identity with
`maximum_bytes_billed` and labels; copilot results come through the guarded session (live
tables, independent path), exactly as a request would compute them.

Ratios are rebuilt the way LookML does: SUM(numerator) / SUM(denominator) x the unit
multiplier, at the grain of the slice.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from copilot.config import Settings
from copilot.marts.views import NON_ADDITIVE, UNIT_MULTIPLIER, components, monthly_metrics
from copilot.packs.compiler import MetricRequest, compile_metric

pytestmark = pytest.mark.bigquery

SAMPLES = [
    # (grain, dims, start, end, filters)
    ("month", ("period", "merchant_country"), date(2025, 6, 1), date(2025, 6, 30), {}),  # R1
    ("month", ("period", "merchant_country"), date(2025, 8, 1), date(2025, 9, 30), {}),  # D1-D4
    (
        "quarter",
        ("period", "mcc_group"),
        date(2025, 7, 1),
        date(2025, 9, 30),
        {"merchant_country": ("IT", "ES")},
    ),
]


@pytest.fixture(scope="module")
def env(pack):
    from copilot.data.bigquery_clients import make_client
    from copilot.data.session import open_session

    settings = Settings.from_env()
    if settings.backend != "bigquery" or not settings.gcp_project_id:
        pytest.skip("showcase project not configured")
    session = open_session(
        settings,
        pack,
        request_id="m8a-consistency",
        component="consistency",
        profile="metric",
        backend="bigquery",
        snapshot_mode="live",
    )
    client = make_client(settings, impersonate=settings.copilot_sa)
    yield settings, session, client
    session.close()


def _mart(settings, client, view: str) -> pd.DataFrame:
    from copilot.data.bigquery_clients import query_job_config

    sql = f"SELECT * FROM `{settings.gcp_project_id}.{settings.dataset_prefix}_marts.{view}`"
    cfg = query_job_config(settings, "consistency", "m8a-consistency")
    rows = client.query(sql, job_config=cfg).result()
    return pd.DataFrame([dict(r) for r in rows])


@pytest.fixture(scope="module")
def marts(env):
    settings, _, client = env
    return {v: _mart(settings, client, v) for v in ("mart_ewi_components", "mart_ewi_monthly")}


def _copilot(env, pack, mid, grain, dims, start, end, filters):
    _, session, _ = env
    req = MetricRequest(mid, start, end, pack.pack.default_as_of, grain, dims, filters)
    q = compile_metric(pack, req, "independent", "bigquery", session.resolver)
    df, _ = session.backend.execute(q.sql, q.params)
    df["period"] = pd.to_datetime(df["period"]).dt.date
    return df


@pytest.mark.parametrize("grain,dims,start,end,filters", SAMPLES)
def test_component_measures_equal_copilot(env, marts, pack, grain, dims, start, end, filters):
    comp = marts["mart_ewi_components"].copy()
    comp["period"] = (
        pd.to_datetime(comp["period_month"])
        .dt.to_period("Q" if grain == "quarter" else "M")
        .dt.start_time.dt.date
    )
    comp = comp[(comp["period"] >= start) & (comp["period"] <= end)]
    for k, vals in filters.items():
        comp = comp[comp[k].isin(vals)]
    cols = list(components(pack))
    sums = comp.groupby(list(dims), dropna=False)[cols].sum().reset_index()
    for c in cols:
        sums[c] = sums[c].astype(float)
    for mid, m in sorted(pack.metrics.items()):
        if mid in NON_ADDITIVE:
            continue
        ref = _copilot(env, pack, mid, grain, dims, start, end, filters)
        merged = ref.merge(
            sums, on=list(dims), how="outer", suffixes=("_copilot", ""), indicator=True
        )
        assert (merged["_merge"] == "both").all(), f"{mid}: slices differ"
        if m.numerator is None:
            mart = merged[mid]
            copilot = merged[f"{mid}_copilot"]
        else:
            num, den = merged[m.numerator], merged[m.denominator]
            mart = num / den.where(den != 0) * UNIT_MULTIPLIER[m.unit]
            copilot = merged[mid]
        pd.testing.assert_series_equal(
            mart.astype(float), copilot.astype(float), check_names=False, rtol=1e-9, atol=1e-9
        )


def test_monthly_values_equal_copilot(env, marts, pack):
    mon = marts["mart_ewi_monthly"].copy()
    mon["period"] = pd.to_datetime(mon["period_month"]).dt.date
    for mid in monthly_metrics(pack):
        ref = _copilot(
            env,
            pack,
            mid,
            "month",
            ("period", "merchant_country"),
            date(2025, 6, 1),
            date(2025, 9, 30),
            {},
        )
        ref = ref[ref["merchant_country"] != pack.pack.unknown_country_code]
        got = mon[(mon["metric_id"] == mid) & (mon["period"] >= date(2025, 6, 1))]
        m = ref.merge(got, on=["period", "merchant_country"], how="outer", indicator=True)
        assert (m["_merge"] == "both").all(), mid
        pd.testing.assert_series_equal(
            m["value"].astype(float), m[mid].astype(float), check_names=False, rtol=1e-9, atol=1e-9
        )


def test_dashboard_story_on_the_default_dataset(marts, env):
    """DASHBOARD_SPEC section 6: R1 is red in the heatmap; the September dip; XX visible."""
    settings, _, client = env
    mon = marts["mart_ewi_monthly"]
    mon = mon.assign(period=pd.to_datetime(mon["period_month"]).dt.date)
    it_jun = mon[
        (mon.metric_id == "chargeback_rate_value")
        & (mon.merchant_country == "IT")
        & (mon.period == date(2025, 6, 1))
    ]
    assert it_jun["rag_status"].tolist() == ["RED"]
    comp = marts["mart_ewi_components"]
    comp = comp.assign(period=pd.to_datetime(comp["period_month"]).dt.date)
    by_month = comp.groupby("period")["settled_volume_eur"].sum().astype(float)
    assert by_month[date(2025, 9, 1)] < 0.75 * by_month[date(2025, 8, 1)]
    assert float(comp[comp.merchant_country == "XX"]["settled_volume_eur"].sum()) > 0
    dq = _mart(settings, client, "mart_dq_status")
    dq = dq.assign(period=pd.to_datetime(dq["period_month"]).dt.date)
    fails = dq[dq.status == "FAIL"]
    assert set(fails[fails.period <= date(2025, 7, 1)]["check_id"]) == set()
    aug = fails[fails.period == date(2025, 8, 1)]
    assert set(aug["merchant_country"]) == {"ES"}
    assert {"V-PK-01", "V-DUAL-01", "V-REC-01"} <= set(aug["check_id"])
    assert {"V-TIME-01", "V-COMP-01", "V-DEF-01"} <= set(
        fails[fails.period == date(2025, 9, 1)]["check_id"]
    )
