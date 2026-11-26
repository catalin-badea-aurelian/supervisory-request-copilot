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


class ParquetExecutor:
    """Local-mode executor bound to a Parquet snapshot (M3).

    An in-memory DuckDB connection exposes one view per snapshot file and nothing else; the
    files are re-verified against their SHA-256 when the executor opens. DuckDB has no
    statement timeout, so a timer interrupts a query that runs too long.
    """

    dialect = "duckdb"

    def __init__(self, snapshot: Any) -> None:
        snapshot.verify()
        self.snapshot = snapshot
        self._con = duckdb.connect(":memory:")
        self._con.execute("SET TimeZone='UTC'")
        paths = {
            t: snapshot.path(t).resolve().as_posix().replace("'", "''")
            for t in sorted(snapshot.files)
        }
        allowed = ", ".join(f"'{p}'" for p in paths.values())
        self._con.execute(f"SET allowed_paths=[{allowed}]")
        for table, path in paths.items():
            self._con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        # only the snapshot files can be read, and the configuration cannot be changed back
        self._con.execute("SET enable_external_access=false")
        self._con.execute("SET lock_configuration=true")

    def dry_run(self, sql: str, params: dict[str, Any], labels: dict[str, str]) -> int | None:
        return None  # no byte accounting in local mode

    def run(
        self, sql: str, params: dict[str, Any], labels: dict[str, str], timeout_s: float
    ) -> tuple[pd.DataFrame, int | None]:
        import threading

        from copilot.data.guarded import QueryTimeout

        timer = threading.Timer(timeout_s, self._con.interrupt)
        timer.start()
        try:
            return self._con.execute(sql, params).fetchdf(), None
        except duckdb.InterruptException:
            raise QueryTimeout(f"query exceeded {timeout_s}s") from None
        finally:
            timer.cancel()

    def describe(self, table_ref: str) -> list[dict[str, str]]:
        if table_ref not in self.snapshot.files:
            raise KeyError(table_ref)
        rows = self._con.execute(f"DESCRIBE {table_ref}").fetchall()
        return [{"name": r[0], "type": str(r[1])} for r in rows]

    def close(self) -> None:
        self._con.close()
