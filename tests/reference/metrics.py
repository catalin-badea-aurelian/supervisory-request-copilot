"""Pandas reference implementation of every glossary metric.

Written independently of the SQL templates (no shared code): it works on the raw Parquet
tables with integer cents and plain pandas. Used as the oracle by the M1 golden tests and,
later, by the evals. Output columns mirror the templates: dimensions, components, metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

SETTLED = ("settled", "refunded")


@dataclass
class Frames:
    merchants: pd.DataFrame
    txns: pd.DataFrame
    chargebacks: pd.DataFrame
    fraud: pd.DataFrame


def _cents(table, col: str) -> np.ndarray:
    arr = pc.multiply(table[col].cast("float64"), 100.0)
    return np.rint(arr.to_numpy()).astype("int64")


def load_frames(parquet_dir: Path) -> Frames:
    def read(name: str):
        return pq.read_table(parquet_dir / f"{name}.parquet")

    m = read("src_merchants").to_pandas()
    t = read("src_transactions")
    tx = t.to_pandas().drop(columns=["amount_eur"])
    tx["cents"] = _cents(t, "amount_eur")
    c = read("src_chargebacks")
    cb = c.to_pandas().drop(columns=["amount_eur"])
    cb["cents"] = _cents(c, "amount_eur")
    f = read("src_fraud_losses")
    fr = f.to_pandas().drop(columns=["loss_eur"])
    fr["cents"] = _cents(f, "loss_eur")
    for df in (m, tx, cb, fr):
        df["_loaded_at"] = pd.to_datetime(df["_loaded_at"], utc=True)
    tx["txn_ts"] = pd.to_datetime(tx["txn_ts"], utc=True)
    return Frames(m, tx, cb, fr)


def _loaded_by(df: pd.DataFrame, as_of: date) -> pd.Series:
    return df["_loaded_at"].dt.date <= as_of


def _period_start(ts: pd.Series, grain: str) -> pd.Series:
    freq = "Q" if grain == "quarter" else "M"
    return ts.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period(freq).dt.start_time.dt.date


def _base(
    fr: Frames,
    period_start: date,
    period_end: date,
    as_of: date,
    grain: str,
    filters: dict[str, tuple[str, ...]],
) -> pd.DataFrame:
    t = fr.txns
    lo = pd.Timestamp(period_start, tz="UTC")
    hi = pd.Timestamp(period_end, tz="UTC") + pd.Timedelta(days=1)
    keep = (t["txn_ts"] >= lo) & (t["txn_ts"] < hi) & _loaded_by(t, as_of)
    keep &= t["status"].isin(SETTLED)
    b = t.loc[keep, ["txn_id", "merchant_id", "txn_ts", "payment_method", "status", "cents"]].copy()
    mer = fr.merchants.set_index("merchant_id")
    b["merchant_country"] = b["merchant_id"].map(mer["merchant_country"]).fillna("XX")
    b["mcc_group"] = b["merchant_id"].map(mer["mcc_group"])
    b["period"] = _period_start(b["txn_ts"], grain)
    cb = fr.chargebacks[_loaded_by(fr.chargebacks, as_of)]
    cb_by_txn = cb.groupby("txn_id").agg(cb_cents=("cents", "sum"), cb_n=("cents", "size"))
    b = b.join(cb_by_txn, on="txn_id")
    b[["cb_cents", "cb_n"]] = b[["cb_cents", "cb_n"]].fillna(0).astype("int64")
    fl = fr.fraud[_loaded_by(fr.fraud, as_of)]
    b = b.join(fl.groupby("txn_id")["cents"].sum().rename("fraud_cents"), on="txn_id")
    b["fraud_cents"] = b["fraud_cents"].fillna(0).astype("int64")
    for key, values in filters.items():
        b = b[b[key].isin(values)]
    return b


def _agg(b: pd.DataFrame, dims: list[str]) -> pd.core.groupby.DataFrameGroupBy | pd.DataFrame:
    if dims:
        return b.groupby(dims, sort=True)
    return b.assign(_all=0).groupby("_all")


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    return num.astype("float64") / den.astype("float64").where(den != 0)


def reference_metric(
    fr: Frames,
    metric_id: str,
    period_start: date,
    period_end: date,
    as_of: date,
    grain: str = "month",
    dims: tuple[str, ...] = (),
    filters: dict[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    b = _base(fr, period_start, period_end, as_of, grain, filters or {})
    d = list(dims)
    if metric_id == "top10_merchant_concentration":
        return _top10(b, d)
    if metric_id == "active_merchants":
        out = _agg(b, d).agg(active_merchants=("merchant_id", "nunique"))
        return _finish(out, d)
    g = _agg(b, d)
    out = g.agg(
        vol=("cents", "sum"),
        n=("cents", "size"),
        cb_c=("cb_cents", "sum"),
        cb_n=("cb_n", "sum"),
        fr_c=("fraud_cents", "sum"),
    )
    refunded = b[b["status"] == "refunded"]
    ref = _agg(refunded, d).agg(ref_c=("cents", "sum")) if len(refunded) else None
    out = out.join(ref) if ref is not None else out.assign(ref_c=0)
    out["ref_c"] = out["ref_c"].fillna(0)
    e = pd.DataFrame(index=out.index)
    if metric_id == "settled_volume_eur":
        e["settled_volume_eur"] = out["vol"] / 100
    elif metric_id == "transaction_count":
        e["transaction_count"] = out["n"]
    elif metric_id == "avg_ticket_eur":
        e["settled_volume_eur"] = out["vol"] / 100
        e["transaction_count"] = out["n"]
        e["avg_ticket_eur"] = _ratio(out["vol"], out["n"]) / 100
    elif metric_id == "chargeback_amount_eur":
        e["chargeback_amount_eur"] = out["cb_c"] / 100
    elif metric_id == "chargeback_rate_value":
        e["chargeback_amount_eur"] = out["cb_c"] / 100
        e["settled_volume_eur"] = out["vol"] / 100
        e["chargeback_rate_value"] = _ratio(out["cb_c"], out["vol"])
    elif metric_id == "chargeback_rate_count":
        e["chargeback_count"] = out["cb_n"]
        e["transaction_count"] = out["n"]
        e["chargeback_rate_count"] = _ratio(out["cb_n"], out["n"]) * 1000
    elif metric_id == "fraud_loss_rate_bps":
        e["fraud_loss_eur"] = out["fr_c"] / 100
        e["settled_volume_eur"] = out["vol"] / 100
        e["fraud_loss_rate_bps"] = _ratio(out["fr_c"], out["vol"]) * 10000
    elif metric_id == "refund_rate_value":
        e["refunded_amount_eur"] = out["ref_c"] / 100
        e["settled_volume_eur"] = out["vol"] / 100
        e["refund_rate_value"] = _ratio(out["ref_c"], out["vol"])
    else:
        raise KeyError(metric_id)
    return _finish(e, d)


def _top10(b: pd.DataFrame, d: list[str]) -> pd.DataFrame:
    keys = [*d, "merchant_id"]
    mv = b.groupby(keys, sort=True)["cents"].sum().rename("vol").reset_index()
    mv = mv.sort_values([*d, "vol", "merchant_id"], ascending=[True] * len(d) + [False, True])
    if d:
        mv["rank"] = mv.groupby(d).cumcount() + 1
        total = mv.groupby(d)["vol"].sum()
        top = mv[mv["rank"] <= 10].groupby(d)["vol"].sum()
    else:
        mv["rank"] = np.arange(1, len(mv) + 1)
        total = pd.Series({0: mv["vol"].sum()})
        top = pd.Series({0: mv.loc[mv["rank"] <= 10, "vol"].sum()})
    e = pd.DataFrame({"top10_volume_eur": top / 100, "settled_volume_eur": total / 100})
    e["top10_merchant_concentration"] = _ratio(top, total)
    return _finish(e, d)


def _finish(e: pd.DataFrame, d: list[str]) -> pd.DataFrame:
    if d:
        return e.reset_index()
    return e.reset_index(drop=True)
