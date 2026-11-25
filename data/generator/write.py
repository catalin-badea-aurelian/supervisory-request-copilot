"""Write the generated tables as Parquet plus a manifest of canonical and file hashes."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from copilot.data.hashing import canonical_hash, sha256_file
from data.generator import constants as C

TS = pa.timestamp("us", tz="UTC")
MONEY = pa.decimal128(18, 2)

# column -> arrow type, in output order. 'cents:<col>' means: money from integer cents column.
SCHEMAS: dict[str, list[tuple[str, pa.DataType, str | None]]] = {
    "src_merchants": [
        ("merchant_id", pa.string(), None),
        ("merchant_country", pa.string(), None),
        ("mcc_group", pa.string(), None),
        ("onboarding_date", pa.date32(), None),
        ("risk_tier", pa.string(), None),
        ("status", pa.string(), None),
        ("_loaded_at", TS, None),
    ],
    "src_transactions": [
        ("txn_id", pa.string(), None),
        ("merchant_id", pa.string(), None),
        ("txn_ts", TS, None),
        ("amount_eur", MONEY, "amount_cents"),
        ("payment_method", pa.string(), None),
        ("status", pa.string(), None),
        ("consumer_country", pa.string(), None),
        ("_loaded_at", TS, None),
    ],
    "src_chargebacks": [
        ("chargeback_id", pa.string(), None),
        ("txn_id", pa.string(), None),
        ("cb_ts", TS, None),
        ("reason_group", pa.string(), None),
        ("amount_eur", MONEY, "amount_cents"),
        ("outcome", pa.string(), None),
        ("_loaded_at", TS, None),
    ],
    "src_fraud_losses": [
        ("loss_id", pa.string(), None),
        ("txn_id", pa.string(), None),
        ("detected_ts", TS, None),
        ("loss_eur", MONEY, "loss_cents"),
        ("_loaded_at", TS, None),
    ],
    "grp_merchant_monthly": [
        ("month", pa.date32(), None),
        ("merchant_country", pa.string(), None),
        ("mcc_group", pa.string(), None),
        ("txn_count", pa.int64(), None),
        ("settled_volume_eur", MONEY, "settled_cents"),
        ("cb_count", pa.int64(), None),
        ("cb_amount_eur", MONEY, "cb_cents"),
        ("cb_rate", pa.float64(), None),
        ("_loaded_at", TS, None),
    ],
    "ledger_control_totals": [
        ("period_month", pa.date32(), None),
        ("merchant_country", pa.string(), None),
        ("measure", pa.string(), None),
        ("amount_eur", MONEY, "cents"),
    ],
    "prior_submissions": [
        ("submission_id", pa.string(), None),
        ("submitted_on", pa.date32(), None),
        ("metric_id", pa.string(), None),
        ("period", pa.string(), None),
        ("merchant_country", pa.string(), None),
        ("value", pa.float64(), None),
    ],
    "meta_load_log": [
        ("table_name", pa.string(), None),
        ("partition", pa.string(), None),
        ("loaded_at", TS, None),
        ("max_event_ts", TS, None),
        ("row_count", pa.int64(), None),
        ("expected_row_count", pa.int64(), None),
    ],
    "ewi_thresholds": [
        ("metric_id", pa.string(), None),
        ("direction", pa.string(), None),
        ("amber", pa.float64(), None),
        ("red", pa.float64(), None),
        ("owner", pa.string(), None),
        ("version", pa.string(), None),
    ],
}


def _money(cents: np.ndarray) -> pa.Array:
    vals = [Decimal(int(c)).scaleb(-2) for c in cents]
    return pa.array(vals, type=MONEY)


def to_arrow(name: str, df: pd.DataFrame) -> pa.Table:
    arrays, fields = [], []
    for col, typ, cents_src in SCHEMAS[name]:
        if cents_src:
            arr = _money(df[cents_src].to_numpy())
        elif typ == TS:
            raw = pd.to_datetime(df[col]).to_numpy().astype("datetime64[us]")
            arr = pa.array(raw, type=pa.timestamp("us")).cast(TS)
        elif typ == pa.date32():
            raw = pd.to_datetime(df[col]).to_numpy().astype("datetime64[D]")
            arr = pa.array(raw, type=pa.date32())
        else:
            values = df[col].astype(object).where(df[col].notna(), None).tolist()
            arr = pa.array(values, type=typ)
        arrays.append(arr)
        fields.append(pa.field(col, typ))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def write_dataset(frames: dict[str, pd.DataFrame], out_dir: Path, seed: int, scale: float) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name, df in frames.items():
        table = to_arrow(name, df)
        path = out_dir / f"{name}.parquet"
        pq.write_table(
            table,
            path,
            compression="zstd",
            use_dictionary=False,
            write_statistics=False,
            data_page_version="2.0",
        )
        tables[name] = {
            "file": path.name,
            "rows": table.num_rows,
            "canonical_sha256": canonical_hash(table),
            "file_sha256_informational": sha256_file(path),
        }
    manifest = {
        "generator_version": C.GENERATOR_VERSION,
        "seed": seed,
        "scale": scale,
        "tables": tables,
    }
    # Note: whether defects were planted is deliberately not recorded here.
    (out_dir / "data_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest
