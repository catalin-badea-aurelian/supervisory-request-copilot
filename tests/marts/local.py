"""Compatibility shim: the DuckDB mart harness lives in copilot.marts.local."""

from copilot.marts.local import connect, query_view, view_sql_duckdb

__all__ = ["connect", "query_view", "view_sql_duckdb"]
