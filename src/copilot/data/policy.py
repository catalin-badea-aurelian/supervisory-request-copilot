"""SQL policy enforced by a parser, not by prompts (ARCHITECTURE section 7.2).

Every statement goes through `SqlPolicy.check` before it can reach a backend:

1. exactly one statement, whose root is a SELECT (optionally WITH ...) or a set operation
2. no DDL, DML, scripting, EXPORT, COPY, ATTACH, PRAGMA, INSTALL/LOAD or raw commands
   anywhere in the tree (including inside CTEs and subqueries)
3. no table functions (read_parquet, EXTERNAL_QUERY, ...) and no file-reading functions
4. every table reference (other than a CTE name) is in the allowlist; INFORMATION_SCHEMA is
   never allowed

`enforce_limit` forces a row cap on exploratory queries (sample_rows).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n)
    for n in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Command",
        "Copy",
        "Export",
        "Pragma",
        "Set",
        "Declare",
        "Use",
        "Attach",
        "Detach",
        "Install",
        "Transaction",
        "Commit",
        "Rollback",
        "Grant",
        "Revoke",
        "Describe",
        "Show",
        "Into",
    )
    if hasattr(exp, n)
)
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)
DENIED_FUNCTION_PREFIXES = (
    "read_",
    "external_query",
    "glob",
    "getenv",
    "query_table",
    "sniff_csv",
    "parquet_",
    "iceberg_",
    "delta_scan",
    "load_file",
)


class PolicyViolation(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PolicyResult:
    statement: str  # root node type, e.g. "Select"
    tables: tuple[str, ...]  # normalised table names the statement reads


def normalise_table_name(name: str) -> str:
    """`proj.ds.t`, "`proj.ds.t`" and `proj`.`ds`.`t` all become proj.ds.t (lowercase)."""
    return name.replace("`", "").replace('"', "").strip().lower()


def _table_name(t: exp.Table) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return normalise_table_name(".".join(parts))


class SqlPolicy:
    def __init__(self, dialect: str, allowed_tables: Iterable[str]) -> None:
        self.dialect = dialect
        self.allowed = frozenset(normalise_table_name(t) for t in allowed_tables)

    def parse(self, sql: str) -> Any:
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except sqlglot.errors.ParseError as e:
            raise PolicyViolation("unparseable", str(e).splitlines()[0]) from None
        if len(statements) != 1:
            raise PolicyViolation("not_single_statement", f"{len(statements)} statements")
        return statements[0]

    def check(self, sql: str) -> PolicyResult:
        tree = self.parse(sql)
        if not isinstance(tree, ALLOWED_ROOTS):
            raise PolicyViolation("not_a_select", type(tree).__name__)
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                raise PolicyViolation("forbidden_statement", type(node).__name__)
            if isinstance(node, (exp.Anonymous, exp.Func)):
                fname = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
                if fname.startswith(DENIED_FUNCTION_PREFIXES):
                    raise PolicyViolation("forbidden_function", fname)
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        tables: set[str] = set()
        for t in tree.find_all(exp.Table):
            if not isinstance(t.this, exp.Identifier):
                raise PolicyViolation("table_function", t.sql(dialect=self.dialect)[:80])
            name = _table_name(t)
            if "information_schema" in name:
                raise PolicyViolation("information_schema", name)
            if not t.catalog and not t.db and name in ctes:
                continue
            if name not in self.allowed:
                raise PolicyViolation("table_not_allowed", name)
            tables.add(name)
        return PolicyResult(type(tree).__name__, tuple(sorted(tables)))


def enforce_limit(sql: str, max_rows: int, dialect: str) -> str:
    """Return the statement with LIMIT <= max_rows (added, lowered, or kept if smaller)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    limit = tree.args.get("limit")
    current = None
    if limit is not None:
        value = limit.expression if isinstance(limit, exp.Limit) else limit
        try:
            current = int(value.name)
        except (TypeError, ValueError):
            current = None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows)  # type: ignore[attr-defined]
    return tree.sql(dialect=dialect)
