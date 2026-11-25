"""DuckDB (local mode): load Parquet into a database file, and a read-only query helper.

The guarded adapter (SQL policy, limits, snapshots) is built in M3; this module only
provides what M1 needs: loading and plain read-only execution for golden tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

TABLES = [
    "src_merchants",
    "src_transactions",
    "src_chargebacks",
    "src_fraud_losses",
    "grp_merchant_monthly",
    "ledger_control_totals",
    "prior_submissions",
    "meta_load_log",
    "ewi_thresholds",
]


def load_parquet_into_duckdb(parquet_dir: Path, db_path: Path) -> None:
    """(Re)create the database file from the Parquet files; one table per file."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        con.execute("SET TimeZone='UTC'")
        for name in TABLES:
            path = (parquet_dir / f"{name}.parquet").as_posix()
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path}')")
    finally:
        con.close()


class DuckDBBackend:
    """Read-only connection with BigQuery-style named parameters ($name)."""

    dialect = "duckdb"

    def __init__(self, db_path: Path) -> None:
        self._con = duckdb.connect(str(db_path), read_only=True)
        self._con.execute("SET TimeZone='UTC'")

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        bound = {k: (v if not isinstance(v, date) else v) for k, v in (params or {}).items()}
        return self._con.execute(sql, bound).fetchdf()

    def close(self) -> None:
        self._con.close()
