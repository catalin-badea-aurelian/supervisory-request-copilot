"""Render the validation engine's own SQL (control-table reads, key and row checks).

Templates in `validation/sql/` are BigQuery Standard SQL with Jinja placeholders. They are
normalised and transpiled with sqlglot exactly like metric templates. Unlike metric
templates, they may read control tables (ledger, load log, prior submissions): the checker
needs them, and the maker must not see them. Every value spliced into the SQL comes from
the domain pack or from validated requests, never from free text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sqlglot
from jinja2 import Environment, StrictUndefined

from copilot.packs.models import DomainPack, SourceDef

SQL_DIR = Path(__file__).parent / "sql"
_PARAM_RE = re.compile(r"(?:@|\$)([a-z_]+)")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CheckSqlError(ValueError):
    pass


def ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise CheckSqlError(f"not a plain identifier: {name!r}")
    return name


def literals(values: Any) -> str:
    vals = sorted(str(v) for v in values)
    if not vals:
        raise CheckSqlError("empty literal list")
    return ", ".join("'" + v.replace("'", "''") + "'" for v in vals)


def period_expr(src: SourceDef) -> str:
    col = ident(src.period_column or "")
    if src.period_column_type == "timestamp":
        return f"DATE_TRUNC(DATE({col}), MONTH)"
    return f"DATE_TRUNC({col}, MONTH)"


def period_filter(src: SourceDef) -> str:
    col = ident(src.period_column or "")
    if src.period_column_type == "timestamp":
        return (
            f"AND {col} >= TIMESTAMP(@period_start) "
            f"AND {col} < TIMESTAMP(DATE_ADD(@period_end, INTERVAL 1 DAY))"
        )
    return f"AND {col} BETWEEN @period_start AND @period_end"


def render_check_sql(
    pack: DomainPack,
    template: str,
    dialect: str,
    resolver: Any,
    params: dict[str, Any],
    **context: Any,
) -> tuple[str, dict[str, Any]]:
    """Render `sql/<template>.sql.j2`; return the transpiled SQL and the parameters it uses."""

    def table(name: str) -> str:
        if name not in pack.pack.sources:
            raise CheckSqlError(f"unknown table {name!r}")
        if dialect == "bigquery":
            if resolver is None:
                raise CheckSqlError("a source resolver is required for BigQuery")
            return str(resolver(name))
        return ident(name)

    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    env.filters["literals"] = literals
    env.filters["ident"] = ident
    text = (SQL_DIR / f"{template}.sql.j2").read_text(encoding="utf-8")
    rendered = env.from_string(text).render(table=table, **context)
    sql = sqlglot.parse_one(rendered, read="bigquery").sql(dialect=dialect)
    used = set(_PARAM_RE.findall(sql))
    return sql, {k: v for k, v in params.items() if k in used}
