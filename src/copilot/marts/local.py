"""Run the generated mart views on DuckDB (local mode, tests): the BigQuery SQL, transpiled."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import sqlglot

from copilot.marts.views import VIEWS
from copilot.ops.schema import OPS_TABLES
from copilot.packs.models import DomainPack

BARE = lambda layer, table: table  # noqa: E731


def view_sql_duckdb(pack: DomainPack, name: str) -> str:
    return sqlglot.transpile(VIEWS[name](pack, BARE), read="bigquery", write="duckdb")[0]


def connect(parquet_dir: Path, ops: dict[str, list[dict[str, Any]]] | None = None):
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")  # as DuckDBBackend and BigQuery: DATE(ts) in UTC
    for p in sorted(parquet_dir.glob("*.parquet")):
        con.execute(f"CREATE VIEW {p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')")
    for table, spec in OPS_TABLES.items():
        rows = (ops or {}).get(table, [])
        df = pd.DataFrame(rows, columns=spec.column_names)
        for c in spec.columns:
            if c.type == "DATE":
                df[c.name] = pd.to_datetime(df[c.name]).dt.date
            elif c.type == "TIMESTAMP":
                df[c.name] = pd.to_datetime(df[c.name], utc=True)
        con.register(f"_{table}", df)
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM _{table}")
    return con


def query_view(con, pack: DomainPack, name: str) -> pd.DataFrame:
    return con.execute(view_sql_duckdb(pack, name)).df()
