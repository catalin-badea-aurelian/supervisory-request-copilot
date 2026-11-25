"""Assemble all tables of one dataset (default with defects, or clean)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from copilot.packs.loader import load_pack
from data.generator import constants as C
from data.generator import defects as defects_mod
from data.generator import derive
from data.generator.world import generate_clean_world


def _ewi_rows() -> pd.DataFrame:
    pack = load_pack("payments")
    rows = [
        {
            "metric_id": m.id,
            "direction": m.ewi.direction,
            "amber": m.ewi.amber,
            "red": m.ewi.red,
            "owner": m.ewi.owner,
            "version": m.ewi.version,
        }
        for m in pack.metrics.values()
        if m.ewi
    ]
    return pd.DataFrame(rows).sort_values("metric_id").reset_index(drop=True)


def _by_month(ts: pd.Series) -> pd.Series:
    return pd.Series(ts.to_numpy().astype("datetime64[M]").astype("datetime64[us]"), index=ts.index)


ALL_DEFECTS = ("D1", "D2", "D3", "D4")


def build_dataset(
    seed: int = C.SEED, scale: float = 1.0, defects: tuple[str, ...] = ALL_DEFECTS
) -> dict[str, pd.DataFrame]:
    world = generate_clean_world(seed, scale)
    clean_merchants, clean_txns = world.merchants, world.txns
    cb, fl = world.chargebacks, world.fraud_losses

    ledger = derive.build_ledger(clean_merchants, clean_txns, cb)
    prior = derive.build_prior_submissions(clean_merchants, clean_txns, cb)
    clean_group = derive.build_group(clean_merchants, clean_txns, cb)

    active = set(defects)
    unknown = active - set(ALL_DEFECTS)
    if unknown:
        raise ValueError(f"unknown defect(s): {sorted(unknown)}")
    merchants = (
        defects_mod.apply_d2_missing_country(clean_merchants, seed)
        if "D2" in active
        else clean_merchants
    )
    txns = defects_mod.apply_d3_late_load(clean_txns) if "D3" in active else clean_txns
    group = derive.build_group(
        merchants,
        txns,
        cb,
        drift_from=pd.Timestamp(C.D4_FROM_MONTH) if "D4" in active else None,
    )
    if "D1" in active:
        group = defects_mod.apply_d1_duplicate_group_rows(group)

    cb_month = _by_month(cb["cb_ts"])
    fl_month = _by_month(fl["detected_ts"])
    cb_obs = cb.assign(_month=cb_month)
    fl_obs = fl.assign(_month=fl_month)
    observed = {
        "src_transactions": (txns.assign(_month=txns["txn_month"]), "txn_ts", "_month"),
        "src_chargebacks": (cb_obs, "cb_ts", "_month"),
        "src_fraud_losses": (fl_obs, "detected_ts", "_month"),
        "grp_merchant_monthly": (group.assign(_month=group["month"]), "month", "_month"),
    }
    expected = {
        "src_transactions": clean_txns.groupby("txn_month").size(),
        "src_chargebacks": cb_obs.groupby("_month").size(),
        "src_fraud_losses": fl_obs.groupby("_month").size(),
        "grp_merchant_monthly": clean_group.groupby("month").size(),
    }
    as_of_excl = datetime.combine(C.DEFAULT_AS_OF, datetime.min.time()) + timedelta(days=1)
    load_log = derive.build_load_log(observed, expected, as_of_excl)

    return {
        "src_merchants": merchants,
        "src_transactions": txns,
        "src_chargebacks": cb,
        "src_fraud_losses": fl,
        "grp_merchant_monthly": group,
        "ledger_control_totals": ledger,
        "prior_submissions": prior,
        "meta_load_log": load_log,
        "ewi_thresholds": _ewi_rows(),
    }
