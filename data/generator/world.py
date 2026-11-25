"""The clean synthetic world: merchants, transactions, chargebacks and fraud losses.

Everything here is defect-free. Defects are applied afterwards (defects.py) so that the
ledger control totals, derived from this clean world, stay the "truth" they reconcile to.
Amounts are held as integer cents (no float rounding differences across platforms).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from data.generator import constants as C


@dataclass
class World:
    merchants: pd.DataFrame
    txns: pd.DataFrame
    chargebacks: pd.DataFrame
    fraud_losses: pd.DataFrame


def months() -> list[date]:
    out, d = [], C.START
    while d <= C.END:
        out.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _ts(day: np.ndarray, sec: np.ndarray) -> np.ndarray:
    """datetime64[us] from day offsets (datetime64[D]) and seconds in day."""
    return day.astype("datetime64[us]") + sec.astype("timedelta64[s]").astype("timedelta64[us]")


def _loaded_next_day(ts: np.ndarray) -> np.ndarray:
    """Daily batch: an event is loaded at 03:00 UTC on the following day."""
    day = ts.astype("datetime64[D]")
    return (day + np.timedelta64(1, "D")).astype("datetime64[us]") + np.timedelta64(3, "h")


def make_merchants(rng: np.random.Generator, n: int) -> pd.DataFrame:
    n_wave = max(4, round(C.D2_WAVE_SHARE * n))
    per_month = max(1, round(0.0075 * n))
    n_window = per_month * 11  # Oct-2024 .. Aug-2025
    n_pre = n - n_wave - n_window

    pre_start = date(2019, 1, 1)
    pre_days = (date(2024, 9, 30) - pre_start).days
    onboard = [pre_start + timedelta(days=int(d)) for d in rng.integers(0, pre_days + 1, n_pre)]
    for m in months()[:11]:
        span = (month_end(m) - m).days + 1
        onboard += [m + timedelta(days=int(d)) for d in rng.integers(0, span, per_month)]
    wave_start = date(2025, 9, 1)
    onboard += [wave_start + timedelta(days=int(d)) for d in rng.integers(0, 10, n_wave)]
    is_wave = np.array([False] * (n_pre + n_window) + [True] * n_wave)

    order = np.argsort(np.array(onboard, dtype="datetime64[D]"), kind="stable")
    onboard_sorted = [onboard[i] for i in order]
    is_wave = is_wave[order]

    country = rng.choice(C.COUNTRIES, size=n, p=C.COUNTRY_WEIGHT)
    mcc = rng.choice(C.MCC_GROUPS, size=n, p=[C.MCC[k][0] for k in C.MCC_GROUPS])
    tier = rng.choice(["low", "medium", "high"], size=n, p=[0.6, 0.3, 0.1])
    status = rng.choice(["active", "suspended", "closed"], size=n, p=[0.97, 0.02, 0.01])
    size = rng.lognormal(mean=0.0, sigma=C.MERCHANT_SIGMA, size=n)
    size = np.where(is_wave, size * C.WAVE_SIZE_MULT, size)
    od = np.array(onboard_sorted, dtype="datetime64[D]")
    return pd.DataFrame(
        {
            "merchant_id": [f"M{i + 1:06d}" for i in range(n)],
            "merchant_country": country,
            "mcc_group": mcc,
            "onboarding_date": od,
            "risk_tier": tier,
            "status": status,
            "size_weight": size,
            "is_wave": is_wave,
            "_loaded_at": _loaded_next_day(od.astype("datetime64[us]")),
        }
    )


def make_transactions(
    rng: np.random.Generator, merchants: pd.DataFrame, total: int
) -> pd.DataFrame:
    season = np.array(C.SEASONALITY)
    per_month = np.rint(total * season / season.sum()).astype(int)
    od = merchants["onboarding_date"].to_numpy().astype("datetime64[D]")
    mcc_idx = merchants["mcc_group"].map({k: i for i, k in enumerate(C.MCC_GROUPS)}).to_numpy()
    ticket_mean = np.array([C.MCC[k][1] for k in C.MCC_GROUPS])
    # merchant size is expressed in value terms: cheap-ticket merchants get more transactions
    w = merchants["size_weight"].to_numpy() / ticket_mean[mcc_idx]
    mu = np.log(ticket_mean) - C.TICKET_SIGMA**2 / 2
    frames = []
    for m, n_m in zip(months(), per_month, strict=True):
        m0 = np.datetime64(m, "D")
        m1 = np.datetime64(month_end(m), "D")
        active = np.flatnonzero(od <= m1)
        lo = np.maximum(od[active], m0)
        span = (m1 - lo).astype(int) + 1
        days_in_month = (m1 - m0).astype(int) + 1
        p = w[active] * span / days_in_month
        p = p / p.sum()
        pick = rng.choice(len(active), size=n_m, p=p)
        mi = active[pick]
        day = lo[pick] + np.floor(rng.random(n_m) * span[pick]).astype("timedelta64[D]")
        sec = rng.integers(0, 86400, n_m)
        z = rng.standard_normal(n_m)
        cents = np.rint(np.exp(mu[mcc_idx[mi]] + C.TICKET_SIGMA * z) * 100).astype("int64")
        cents = np.clip(cents, 100, 2_000_000)
        u = rng.random(n_m)
        status = np.where(
            u < C.P_REFUNDED,
            "refunded",
            np.where(u < C.P_REFUNDED + C.P_AUTHORISED, "authorised", "settled"),
        )
        wallet = rng.random(n_m) < C.WALLET_SHARE
        home = rng.random(n_m) < 0.88
        foreign = rng.choice(C.OTHER_CONSUMER_COUNTRIES, size=n_m)
        frames.append(
            pd.DataFrame(
                {
                    "merchant_idx": mi,
                    "txn_ts": _ts(day, sec),
                    "amount_cents": cents,
                    "payment_method": np.where(wallet, "wallet", "card"),
                    "status": status,
                    "home": home,
                    "foreign": foreign,
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["txn_ts", "merchant_idx"], kind="stable").reset_index(drop=True)
    mer = merchants.reset_index(drop=True)
    idx = df["merchant_idx"].to_numpy()
    df["merchant_id"] = mer["merchant_id"].to_numpy()[idx]
    df["merchant_country"] = mer["merchant_country"].to_numpy()[idx]
    df["mcc_group"] = mer["mcc_group"].to_numpy()[idx]
    df["consumer_country"] = np.where(df["home"], df["merchant_country"], df["foreign"])
    df["txn_id"] = [f"T{i + 1:010d}" for i in range(len(df))]
    df["txn_month"] = df["txn_ts"].to_numpy().astype("datetime64[M]").astype("datetime64[us]")
    df["_loaded_at"] = _loaded_next_day(df["txn_ts"].to_numpy())
    return df.drop(columns=["home", "foreign"])


def _select_events(
    rng: np.random.Generator,
    txns: pd.DataFrame,
    base_value_rate: float,
    exponent: float,
    mcc_mult: dict[str, float],
    country_mult: dict[str, float] | None,
    month_noise: dict[date, float] | None,
    boost: tuple[str, str, date, float] | None,
) -> np.ndarray:
    """Pick event transactions (chargebacks, fraud) per month x country pool.

    Targets a total event value of base_value_rate x sum(multiplier_i x amount_i), sampling
    without replacement with weights amount**exponent x multiplier_i; the last transaction is
    included only if it brings the total closer to the target. Returns row labels of `txns`.
    """
    settled = txns["status"].to_numpy() == "settled"
    sub = txns.loc[settled, ["txn_month", "merchant_country", "mcc_group", "amount_cents"]]
    chosen: list[np.ndarray] = []
    for (month, country), grp in sub.groupby(["txn_month", "merchant_country"], sort=True):
        labels = grp.index.to_numpy()
        a = grp["amount_cents"].to_numpy().astype("float64")
        mon = pd.Timestamp(month).date()
        m = grp["mcc_group"].map(mcc_mult).to_numpy(dtype="float64")
        if country_mult is not None:
            m = m * country_mult[country]
        if month_noise is not None:
            m = m * month_noise[mon]
        if boost is not None and (country, mon) == (boost[0], boost[2]):
            m = np.where(grp["mcc_group"].to_numpy() == boost[1], m * boost[3], m)
        target = base_value_rate * float((m * a).sum())
        keys = -np.log(rng.random(len(a))) / (np.power(a, exponent) * m)
        order = np.argsort(keys, kind="stable")
        cum = np.cumsum(a[order])
        # take the prefix that stays within the target, then top up with the next candidates
        # (in sampling order) that bring the total closer to it
        i = int(np.searchsorted(cum, target, side="right"))
        picked = list(order[:i])
        remaining = target - (cum[i - 1] if i > 0 else 0.0)
        cand = order[i : i + 400]
        used = np.zeros(len(cand), dtype=bool)
        for _ in range(8):
            gap = np.abs(remaining - a[cand])
            gap[used] = np.inf
            k = int(np.argmin(gap)) if len(cand) else -1
            if k < 0 or not gap[k] < abs(remaining):
                break
            used[k] = True
            picked.append(cand[k])
            remaining -= a[cand[k]]
        chosen.append(labels[np.array(picked, dtype="int64")])
    return np.concatenate(chosen) if chosen else np.array([], dtype="int64")


def make_chargebacks(rng: np.random.Generator, txns: pd.DataFrame) -> pd.DataFrame:
    month_noise = {m: float(np.exp(rng.normal(0, C.CB_MONTH_NOISE))) for m in months()}
    labels = _select_events(
        rng,
        txns,
        C.CB_BASE_VALUE_RATE,
        C.CB_AMOUNT_EXPONENT,
        {k: v[2] for k, v in C.MCC.items()},
        C.COUNTRY_CB_MULT,
        month_noise,
        (C.R1_COUNTRY, C.R1_MCC, C.R1_MONTH, C.R1_MULT),
    )
    sel = txns.loc[np.sort(labels)].reset_index(drop=True)
    n = len(sel)
    lag_days = np.clip(np.rint(rng.gamma(3.0, 6.0, n)), 3, 60).astype("int64")
    sec = rng.integers(0, 86400, n)
    cb_ts = sel["txn_ts"].to_numpy() + (lag_days * 86400 + sec).astype("timedelta64[s]").astype(
        "timedelta64[us]"
    )
    reason = rng.choice(
        ["fraud", "goods_not_received", "product_issue", "processing_error", "other"],
        size=n,
        p=[0.35, 0.20, 0.25, 0.10, 0.10],
    )
    is_r1 = (
        (sel["merchant_country"] == C.R1_COUNTRY)
        & (sel["mcc_group"] == C.R1_MCC)
        & (sel["txn_month"] == pd.Timestamp(C.R1_MONTH))
    ).to_numpy()
    reason = np.where(is_r1, "goods_not_received", reason)
    outcome = rng.choice(["won", "lost", "pending"], size=n, p=[0.35, 0.50, 0.15])
    cb = pd.DataFrame(
        {
            "txn_id": sel["txn_id"],
            "cb_ts": cb_ts,
            "reason_group": reason,
            "amount_cents": sel["amount_cents"],
            "outcome": outcome,
        }
    )
    cb = cb[cb["cb_ts"] < np.datetime64(C.EVENT_CUTOFF)]
    cb = cb.sort_values(["cb_ts", "txn_id"], kind="stable").reset_index(drop=True)
    cb.insert(0, "chargeback_id", [f"CB{i + 1:08d}" for i in range(len(cb))])
    cb["outcome"] = np.where(
        cb["cb_ts"] > np.datetime64(datetime(2025, 9, 15)), "pending", cb["outcome"]
    )
    cb["_loaded_at"] = _loaded_next_day(cb["cb_ts"].to_numpy())
    return cb


def make_fraud_losses(rng: np.random.Generator, txns: pd.DataFrame) -> pd.DataFrame:
    labels = _select_events(
        rng,
        txns,
        C.FRAUD_BASE_VALUE_RATE,
        C.FRAUD_AMOUNT_EXPONENT,
        {k: v[3] for k, v in C.MCC.items()},
        None,
        None,
        None,
    )
    sel = txns.loc[np.sort(labels)].reset_index(drop=True)
    n = len(sel)
    lag_days = np.clip(np.rint(rng.gamma(2.0, 4.0, n)), 1, 30).astype("int64")
    sec = rng.integers(0, 86400, n)
    det = sel["txn_ts"].to_numpy() + (lag_days * 86400 + sec).astype("timedelta64[s]").astype(
        "timedelta64[us]"
    )
    fl = pd.DataFrame(
        {"txn_id": sel["txn_id"], "detected_ts": det, "loss_cents": sel["amount_cents"]}
    )
    fl = fl[fl["detected_ts"] < np.datetime64(C.EVENT_CUTOFF)]
    fl = fl.sort_values(["detected_ts", "txn_id"], kind="stable").reset_index(drop=True)
    fl.insert(0, "loss_id", [f"FL{i + 1:08d}" for i in range(len(fl))])
    fl["_loaded_at"] = _loaded_next_day(fl["detected_ts"].to_numpy())
    return fl


def generate_clean_world(seed: int = C.SEED, scale: float = 1.0) -> World:
    rng = np.random.default_rng(seed)
    n_merch = max(80, round(C.N_MERCHANTS * scale)) if scale < 1 else C.N_MERCHANTS
    merchants = make_merchants(rng, n_merch)
    txns = make_transactions(rng, merchants, round(C.N_TRANSACTIONS * scale))
    cb = make_chargebacks(rng, txns)
    fl = make_fraud_losses(rng, txns)
    return World(merchants=merchants, txns=txns, chargebacks=cb, fraud_losses=fl)
