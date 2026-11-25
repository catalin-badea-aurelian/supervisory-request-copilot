"""Compile governed metric templates into SQL for a backend.

Templates are written in BigQuery Standard SQL with Jinja placeholders and named
parameters (@period_start, @period_end, @as_of). The compiler validates every input against
the domain pack, renders the template, normalises it with sqlglot, and transpiles it for the
requested dialect. Dimensions only come from the metric's `allowed_dimensions`; filter values
must be enumerated values of the pack's dimension; the independent path may not read the
parent-group data product.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import sqlglot
from jinja2 import Environment, StrictUndefined

from copilot.packs.models import DomainPack, MetricDef

Dialect = Literal["bigquery", "duckdb"]
SourcePath = Literal["preferred", "independent"]
Grain = Literal["month", "quarter"]

_PARAM_RE = re.compile(r"(?:@|\$)([a-z_]+)")


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class MetricRequest:
    metric_id: str
    period_start: date
    period_end: date
    as_of: date
    grain: Grain = "month"
    dims: tuple[str, ...] = ()
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class CompiledQuery:
    metric_id: str
    metric_version: str
    template_id: str
    path: SourcePath
    dialect: Dialect
    sql: str
    sql_sha256: str
    params: dict[str, Any]


def _validate_period(req: MetricRequest) -> None:
    s, e = req.period_start, req.period_end
    if s > e:
        raise CompileError("period_start is after period_end")
    step = 1 if req.grain == "month" else 3
    if s.day != 1 or (s.month - 1) % step != 0:
        raise CompileError(f"period_start {s} is not aligned to a {req.grain} start")
    last = calendar.monthrange(e.year, e.month)[1]
    if e.day != last or e.month % step != 0:
        raise CompileError(f"period_end {e} is not aligned to a {req.grain} end")


def _literal_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


def _validate(pack: DomainPack, metric: MetricDef, req: MetricRequest) -> None:
    _validate_period(req)
    for d in req.dims:
        if d not in metric.allowed_dimensions:
            raise CompileError(f"dimension {d!r} is not allowed for {metric.id}")
    if len(set(req.dims)) != len(req.dims):
        raise CompileError("duplicate dimensions")
    for key, values in req.filters.items():
        if key == "period" or key not in metric.allowed_dimensions:
            raise CompileError(f"filter on {key!r} is not allowed for {metric.id}")
        allowed = set(pack.pack.dimensions[key].values)
        bad = [v for v in values if v not in allowed]
        if bad:
            raise CompileError(f"unknown value(s) for {key}: {bad}")
        if not values:
            raise CompileError(f"empty filter for {key}")


def _context(req: MetricRequest) -> dict[str, Any]:
    grain = req.grain.upper()
    select_parts = [
        f"DATE_TRUNC(period_date, {grain}) AS period" if d == "period" else d for d in req.dims
    ]
    dims_lead = (", ".join(select_parts) + ", ") if req.dims else ""
    names = ", ".join(req.dims)
    filters_sql = "".join(
        f" AND {k} IN ({_literal_list(tuple(v))})" for k, v in sorted(req.filters.items())
    )
    return {
        "dims": list(req.dims),
        "dims_lead": dims_lead,
        "dim_names": names,
        "dim_names_lead": (names + ", ") if req.dims else "",
        "group_sql": f"GROUP BY {names}" if req.dims else "",
        "filters_sql": filters_sql,
        "grain": grain,
    }


def compile_metric(
    pack: DomainPack,
    req: MetricRequest,
    path: SourcePath = "preferred",
    dialect: Dialect = "duckdb",
    source_resolver: Any = None,
) -> CompiledQuery:
    """Render, validate and transpile one governed query.

    `source_resolver(table_name) -> str` gives the fully qualified table reference for
    BigQuery; DuckDB uses bare table names.
    """
    if req.metric_id not in pack.metrics:
        raise CompileError(f"unknown metric {req.metric_id!r}")
    metric = pack.metrics[req.metric_id]
    if metric.status != "approved":
        raise CompileError(f"metric {metric.id} is not approved")
    _validate(pack, metric, req)

    template_rel = metric.sql_template if path == "preferred" else metric.independent_sql_template
    template_text = (Path(pack.root) / template_rel).read_text(encoding="utf-8")

    def source(name: str) -> str:
        src = pack.pack.sources.get(name)
        if src is None or src.layer == "control":
            raise CompileError(f"source {name!r} is not available to metric templates")
        if path == "independent" and src.layer == "group":
            raise CompileError("the independent path may not read the group data product")
        if dialect == "bigquery":
            if source_resolver is None:
                raise CompileError("a source resolver is required for BigQuery")
            return str(source_resolver(name))
        return name

    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    rendered = env.from_string(template_text).render(source=source, **_context(req))
    try:
        tree = sqlglot.parse_one(rendered, read="bigquery")
    except sqlglot.errors.ParseError as exc:
        raise CompileError(f"template for {metric.id} does not parse: {exc}") from exc
    sql = tree.sql(dialect=dialect)

    all_params: dict[str, Any] = {
        "period_start": req.period_start,
        "period_end": req.period_end,
        "as_of": req.as_of,
    }
    used = set(_PARAM_RE.findall(sql))
    params = {k: v for k, v in all_params.items() if k in used}
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return CompiledQuery(
        metric_id=metric.id,
        metric_version=metric.version,
        template_id=f"{metric.id}@{metric.version}:{path}",
        path=path,
        dialect=dialect,
        sql=sql,
        sql_sha256=digest,
        params=params,
    )
