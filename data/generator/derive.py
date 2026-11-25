"""Derived tables: group data product, ledger control totals, prior submissions, load log."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from data.generator import constants as C
from data.generator.world import month_end, months

SETTLED = ("settled", "refunded")


def _country_or_xx(merchants: pd.DataFrame) -> pd.Series:
    return merchants.set_index("merchant_id")["merchant_country"].fillna("XX")


def group_built_at(month: pd.Timestamp) -> datetime:
    return datetime.combine(
        month_end(month.date()) + timedelta(days=C.GROUP_BUILD_LAG_DAYS), datetime.min.time()
    ) + timedelta(hours=6)


def build_group(
    merchants: pd.DataFrame,
    txns: pd.DataFrame,
    chargebacks: pd.DataFrame,
    drift_from: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """grp_merchant_monthly as the parent group would build it.

    A month's rows only use transactions loaded by the time the product was built (this is
    how an incomplete load propagates), and unknown merchant countries become 'XX'.
    `drift_from` switches cb_rate to a count basis from that month on (planted defect D4).
    """
    country = _country_or_xx(merchants)
    mcc = merchants.set_index("merchant_id")["mcc_group"]
    t = txns[txns["status"].isin(SETTLED)].copy()
    built_at = t["txn_month"].map({m: group_built_at(m) for m in t["txn_month"].unique()})
    t = t[t["_loaded_at"] <= built_at.astype("datetime64[us]")]
    t["merchant_country"] = t["merchant_id"].map(country)
    t["mcc_group"] = t["merchant_id"].map(mcc)
    cb = chargebacks.merge(
        t[["txn_id", "txn_month", "merchant_country", "mcc_group"]], on="txn_id", how="inner"
    )
    keys = ["txn_month", "merchant_country", "mcc_group"]
    a = t.groupby(keys).agg(txn_count=("txn_id", "size"), settled_cents=("amount_cents", "sum"))
    b = cb.groupby(keys).agg(cb_count=("txn_id", "size"), cb_cents=("amount_cents", "sum"))
    g = a.join(b, how="left").fillna({"cb_count": 0, "cb_cents": 0}).reset_index()
    g["cb_count"] = g["cb_count"].astype("int64")
    g["cb_cents"] = g["cb_cents"].astype("int64")
    g["cb_rate"] = g["cb_cents"] / g["settled_cents"]
    if drift_from is not None:
        drift = g["txn_month"] >= drift_from
        g.loc[drift, "cb_rate"] = g.loc[drift, "cb_count"] / g.loc[drift, "txn_count"]
    g["_loaded_at"] = g["txn_month"].map({m: group_built_at(m) for m in g["txn_month"].unique()})
    g["_loaded_at"] = g["_loaded_at"].astype("datetime64[us]")
    g = g.rename(columns={"txn_month": "month"})
    return g.sort_values(["month", "merchant_country", "mcc_group"]).reset_index(drop=True)


def build_ledger(
    merchants: pd.DataFrame, txns: pd.DataFrame, chargebacks: pd.DataFrame
) -> pd.DataFrame:
    """Ledger control totals per month x true country (built from the clean world)."""
    country = merchants.set_index("merchant_id")["merchant_country"]
    t = txns[txns["status"].isin(SETTLED)].copy()
    t["merchant_country"] = t["merchant_id"].map(country)
    vol = t.groupby(["txn_month", "merchant_country"])["amount_cents"].sum().rename("cents")
    cbj = chargebacks.merge(
        t[["txn_id", "txn_month", "merchant_country"]], on="txn_id", how="inner"
    )
    cbv = cbj.groupby(["txn_month", "merchant_country"])["amount_cents"].sum().rename("cents")
    out = []
    for measure, s in (("settled_volume_eur", vol), ("chargeback_amount_eur", cbv)):
        full = s.reindex(
            pd.MultiIndex.from_product(
                [[pd.Timestamp(m) for m in months()], C.COUNTRIES],
                names=["txn_month", "merchant_country"],
            ),
            fill_value=0,
        )
        df = full.reset_index()
        df["measure"] = measure
        out.append(df)
    led = pd.concat(out, ignore_index=True).rename(columns={"txn_month": "period_month"})
    return led.sort_values(["period_month", "merchant_country", "measure"]).reset_index(drop=True)


def build_prior_submissions(
    merchants: pd.DataFrame, txns: pd.DataFrame, chargebacks: pd.DataFrame
) -> pd.DataFrame:
    """Figures 'submitted' for Q4-2024 and Q1-2025, consistent with the clean world."""
    country = merchants.set_index("merchant_id")["merchant_country"]
    t = txns[txns["status"].isin(SETTLED)].copy()
    t["merchant_country"] = t["merchant_id"].map(country)
    t["quarter"] = t["txn_ts"].dt.to_period("Q").astype(str)
    cbj = chargebacks.merge(t[["txn_id", "quarter", "merchant_country"]], on="txn_id", how="inner")
    vol = t.groupby(["quarter", "merchant_country"])["amount_cents"].sum()
    cbv = cbj.groupby(["quarter", "merchant_country"])["amount_cents"].sum()
    rows = []
    plan = {
        "2024Q4": ("2024-Q4", datetime(2025, 2, 5)),
        "2025Q1": ("2025-Q1", datetime(2025, 5, 6)),
    }
    n = 0
    for q, (label, submitted) in plan.items():
        for c in C.COUNTRIES:
            v, cb = int(vol[(q, c)]), int(cbv.get((q, c), 0))
            for metric, value in (
                ("settled_volume_eur", v / 100.0),
                ("chargeback_rate_value", cb / v),
            ):
                n += 1
                rows.append(
                    {
                        "submission_id": f"SUB-{n:04d}",
                        "submitted_on": np.datetime64(submitted.date(), "D"),
                        "metric_id": metric,
                        "period": label,
                        "merchant_country": c,
                        "value": value,
                    }
                )
    return pd.DataFrame(rows)


def build_load_log(
    observed: dict[str, tuple[pd.DataFrame, str, str]],
    expected_counts: dict[str, pd.Series],
    as_of_exclusive: datetime,
) -> pd.DataFrame:
    """Load log as seen at the default as-of date.

    observed[table] = (frame, event_ts_column, month_column); expected_counts[table] is the
    per-month row count of the defect-free world.
    """
    rows = []
    for table, (df, event_col, month_col) in observed.items():
        loaded = df[df["_loaded_at"] < np.datetime64(as_of_exclusive)]
        g = loaded.groupby(month_col).agg(
            loaded_at=("_loaded_at", "max"),
            max_event_ts=(event_col, "max"),
            row_count=("_loaded_at", "size"),
        )
        exp = expected_counts[table]
        for month in sorted(set(exp.index) | set(g.index)):
            rec = g.loc[month] if month in g.index else None
            rows.append(
                {
                    "table_name": table,
                    "partition": pd.Timestamp(month).strftime("%Y-%m"),
                    "loaded_at": rec["loaded_at"] if rec is not None else pd.NaT,
                    "max_event_ts": rec["max_event_ts"] if rec is not None else pd.NaT,
                    "row_count": int(rec["row_count"]) if rec is not None else 0,
                    "expected_row_count": int(exp.get(month, 0)),
                }
            )
    return pd.DataFrame(rows).sort_values(["table_name", "partition"]).reset_index(drop=True)
