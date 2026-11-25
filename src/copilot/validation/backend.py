"""The read-only backend interface the checks rely on (DuckDB and BigQuery both satisfy it)."""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class ReadBackend(Protocol):
    dialect: str

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame: ...
