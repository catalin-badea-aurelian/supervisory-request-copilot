"""Small, pure helpers that make query results comparable across backends."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

NULL_KEY = "NULL"


def iso_date(v: Any) -> str:
    if v is None or v is pd.NaT or (isinstance(v, float) and math.isnan(v)):
        return NULL_KEY
    if isinstance(v, str):
        return v[:10]
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, np.datetime64):
        return pd.Timestamp(v).date().isoformat()
    return str(v)


def to_float(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = s.map(lambda v: float(v) if isinstance(v, (Decimal, int, float)) else v)
    return pd.to_numeric(s, errors="coerce").astype("float64")


def normalise_country(s: pd.Series, unknown: str) -> pd.Series:
    """A missing or empty merchant country becomes the pack's unknown code (XX)."""
    obj = s.astype(object)
    missing = obj.isna() | (obj.astype(str).str.strip() == "")
    return obj.where(~missing, unknown).astype(str)


def normalise(df: pd.DataFrame, keys: list[str], unknown_country: str = "XX") -> pd.DataFrame:
    """Keys as strings (periods as ISO dates, missing country -> unknown code); values as float."""
    out = df.copy()
    for k in keys:
        if k not in out:
            continue
        if k == "period":
            out[k] = out[k].map(iso_date).astype(str)
        elif k == "merchant_country":
            out[k] = normalise_country(out[k], unknown_country)
        else:
            out[k] = out[k].astype(object).where(out[k].notna(), NULL_KEY).astype(str)
    for c in out.columns:
        if c not in keys:
            out[c] = to_float(out[c])
    present = [k for k in keys if k in out]
    if present:
        out = out.sort_values(present).reset_index(drop=True)
    return out


def month_starts(start: date, end: date) -> list[date]:
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = add_months(d, 1)
    return out


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def period_starts(start: date, end: date, grain: str) -> list[date]:
    step = 3 if grain == "quarter" else 1
    return month_starts(start, end)[::step]


def quarter_label(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def rel_diff(a: float, b: float) -> float:
    """Relative difference; 0 when both are zero (or both missing), inf when one is missing."""
    a_nan, b_nan = math.isnan(a), math.isnan(b)
    if a_nan and b_nan:
        return 0.0
    if a_nan or b_nan:
        return math.inf
    base = max(abs(a), abs(b))
    return 0.0 if base == 0 else abs(a - b) / base
