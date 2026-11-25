"""Planted defects D1-D4. Test-only knowledge: never imported by the copilot package."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.generator import constants as C


def apply_d2_missing_country(merchants: pd.DataFrame, seed: int) -> pd.DataFrame:
    """D2: ~40% of the 2025-09 onboarding wave (about 2% of all merchants) lose their country."""
    rng = np.random.default_rng(seed + 2)
    out = merchants.copy()
    wave = np.flatnonzero(out["is_wave"].to_numpy())
    k = max(1, round(C.D2_NULL_FRACTION * len(wave)))
    chosen = rng.choice(wave, size=k, replace=False)
    out["merchant_country"] = out["merchant_country"].astype(object)
    out.iloc[chosen, out.columns.get_loc("merchant_country")] = None
    return out


def apply_d3_late_load(txns: pd.DataFrame) -> pd.DataFrame:
    """D3: transactions from 2025-09-19 onwards are loaded late (after the as-of date)."""
    out = txns.copy()
    late = out["txn_ts"] >= np.datetime64(C.D3_LATE_FROM)
    out.loc[late, "_loaded_at"] = np.datetime64(C.D3_LATE_LOADED_AT, "us")
    return out


def apply_d1_duplicate_group_rows(group: pd.DataFrame) -> pd.DataFrame:
    """D1: a re-processed batch duplicates the ES / 2025-08 rows of the group product."""
    dup = group[
        (group["month"] == pd.Timestamp(C.D1_MONTH)) & (group["merchant_country"] == C.D1_COUNTRY)
    ]
    out = pd.concat([group, dup], ignore_index=True)
    return out.sort_values(["month", "merchant_country", "mcc_group"], kind="stable").reset_index(
        drop=True
    )
