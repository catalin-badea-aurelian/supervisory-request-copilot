"""Generate the LookML project (`looker/`) from the domain pack (DASHBOARD_SPEC section 3).

- one measure per glossary metric, with the glossary formula, a unit-based value format, the
  description `"<id> v<version> | <definition>"` and the tags `metric_id:` / `metric_version:`
  - decomposable metrics live in `ewi_components` as SUM(numerator) / SUM(denominator) x the
    unit multiplier, the same formula as the templates, at any grain the user picks
  - metrics that cannot be re-aggregated (`NON_ADDITIVE`) live in `ewi` at month x country
- only dimensions every metric in a view allows are exposed
- hidden helper measures for components that are not glossary metrics
- three LookML dashboards: risk_overview, data_risk, request_operations

Tables are referenced as `@{DATASET_PREFIX}_marts.<view>`: the Looker connection's project
resolves them, so no project id is written to Git. Every file starts with the generated
header and a content hash, so a hand edit is detectable. Manual changes go to
`looker/refinements/`, which the generator never writes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import yaml

from copilot.marts.views import (
    COMPONENT_DIMS,
    NON_ADDITIVE,
    UNIT_MULTIPLIER,
    components,
    monthly_metrics,
)
from copilot.packs.models import DomainPack, MetricDef

MODEL = "spb_oversight"
CONNECTION = "spb_bigquery"
DISCLAIMER = "Synthetic data — fictional entity."
HASH_RE = re.compile(r"^# content-sha256: ([0-9a-f]{64})$", re.M)

VALUE_FORMAT = {
    "ratio": "percent_2",
    "eur": "eur_0",
    "count": "decimal_0",
    "bps": "bps_1",
    "per_mille": "per_mille_2",
}
NAMED_FORMATS = {
    "eur_0": '"€"#,##0',
    "bps_1": '0.0" bps"',
    "per_mille_2": '0.00" ‰"',
}
DIM_SQL = {
    "period": "period_month",
    "merchant_country": "merchant_country",
    "mcc_group": "mcc_group",
}


# ---- small LookML writer -------------------------------------------------------------------
def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class Block:
    kind: str
    name: str
    lines: list[str]

    def render(self, indent: str = "  ") -> str:
        body = "".join(f"{indent}  {line}\n" for line in self.lines)
        return f"{indent}{self.kind}: {self.name} {{\n{body}{indent}}}\n"


def _sql(expr: str) -> str:
    return f"sql: {expr} ;;"


def dimension(
    name: str,
    typ: str,
    sql: str,
    *,
    pk: bool = False,
    hidden: bool = False,
    label: str | None = None,
    description: str | None = None,
) -> Block:
    lines = []
    if pk:
        lines.append("primary_key: yes")
    if hidden:
        lines.append("hidden: yes")
    lines.append(f"type: {typ}")
    if label:
        lines.append(f"label: {_q(label)}")
    if description:
        lines.append(f"description: {_q(description)}")
    lines.append(_sql(sql))
    return Block("dimension", name, lines)


def period_group(
    sql: str, name: str = "period", timeframes: str = "[month, quarter, year]"
) -> Block:
    return Block(
        "dimension_group",
        name,
        ["type: time", f"timeframes: {timeframes}", "datatype: date", "convert_tz: no", _sql(sql)],
    )


def time_group(name: str, sql: str) -> Block:
    return Block(
        "dimension_group",
        name,
        ["type: time", "timeframes: [raw, time, date, month]", _sql(sql)],
    )


def measure(
    name: str,
    typ: str,
    sql: str | None,
    *,
    fmt: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    hidden: bool = False,
    label: str | None = None,
    filters: dict[str, str] | None = None,
) -> Block:
    lines = []
    if hidden:
        lines.append("hidden: yes")
    lines.append(f"type: {typ}")
    if label:
        lines.append(f"label: {_q(label)}")
    if sql is not None:
        lines.append(_sql(sql))
    for field, value in (filters or {}).items():
        lines.append(f"filters: [{field}: {_q(value)}]")
    if fmt:
        lines.append(f"value_format_name: {fmt}")
    if description:
        lines.append(f"description: {_q(description)}")
    if tags:
        lines.append("tags: [" + ", ".join(_q(t) for t in tags) + "]")
    return Block("measure", name, lines)


def view(name: str, table: str | None, blocks: list[Block], derived_sql: str | None = None) -> str:
    out = f"view: {name} {{\n"
    if table:
        out += f"  sql_table_name: `@{{DATASET_PREFIX}}_marts.{table}` ;;\n"
    out += "\n".join(b.render() for b in blocks)
    return out + "}\n"


# ---- metric metadata -------------------------------------------------------------------------
def description(m: MetricDef) -> str:
    return f"{m.id} v{m.version} | {' '.join(m.definition.split())}"


def metric_tags(m: MetricDef) -> list[str]:
    return [f"metric_id:{m.id}", f"metric_version:{m.version}"]


def ratio_sql(m: MetricDef) -> str:
    mult = UNIT_MULTIPLIER[m.unit]
    base = f"SAFE_DIVIDE(${{{m.numerator}}}, ${{{m.denominator}}})"
    return base if mult == 1 else f"{base} * {mult}"


def component_metrics(pack: DomainPack) -> list[str]:
    """Glossary metrics served by `ewi_components` (additive or decomposable)."""
    return sorted(
        mid for mid, m in pack.metrics.items() if m.status == "approved" and mid not in NON_ADDITIVE
    )


def exposed_dims(pack: DomainPack, metric_ids: list[str], candidates: tuple[str, ...]) -> list[str]:
    return [
        d for d in candidates if all(d in pack.metrics[m].allowed_dimensions for m in metric_ids)
    ]


# ---- views -----------------------------------------------------------------------------------
def view_ewi_components(pack: DomainPack) -> str:
    mids = component_metrics(pack)
    dims = exposed_dims(pack, mids, COMPONENT_DIMS)
    keys = [DIM_SQL[d] for d in dims]
    pk = ", '|', ".join(f"COALESCE(CAST(${{TABLE}}.{k} AS STRING), '')" for k in keys)
    blocks: list[Block] = [dimension("pk", "string", f"CONCAT({pk})", pk=True, hidden=True)]
    for d in dims:
        if d == "period":
            blocks.append(period_group("${TABLE}.period_month"))
        else:
            blocks.append(dimension(d, "string", f"${{TABLE}}.{d}"))
    for comp in components(pack):
        m = pack.metrics.get(comp)
        if m is not None:
            blocks.append(
                measure(
                    comp,
                    "sum",
                    f"${{TABLE}}.{comp}",
                    fmt=VALUE_FORMAT[m.unit],
                    description=description(m),
                    tags=metric_tags(m),
                )
            )
        else:
            fmt = "decimal_0" if comp.endswith("_count") else "eur_0"
            blocks.append(
                measure(
                    comp,
                    "sum",
                    f"${{TABLE}}.{comp}",
                    fmt=fmt,
                    hidden=True,
                    description=f"Component {comp} (helper, not a glossary metric)",
                )
            )
    for mid in mids:
        m = pack.metrics[mid]
        if m.numerator is None:
            continue
        blocks.append(
            measure(
                mid,
                "number",
                ratio_sql(m),
                fmt=VALUE_FORMAT[m.unit],
                description=description(m),
                tags=metric_tags(m),
            )
        )
    return view("ewi_components", "mart_ewi_components", blocks)


def view_ewi(pack: DomainPack) -> str:
    blocks = [
        dimension(
            "pk",
            "string",
            "CONCAT(${TABLE}.metric_id, '|', CAST(${TABLE}.period_month AS STRING), '|', "
            "${TABLE}.merchant_country)",
            pk=True,
            hidden=True,
        ),
        period_group("${TABLE}.period_month"),
        dimension("merchant_country", "string", "${TABLE}.merchant_country"),
        dimension("metric_id", "string", "${TABLE}.metric_id"),
        dimension("metric_version", "string", "${TABLE}.metric_version"),
        dimension("direction", "string", "${TABLE}.direction"),
        dimension("amber", "number", "${TABLE}.amber"),
        dimension("red", "number", "${TABLE}.red"),
        dimension(
            "rag_status",
            "string",
            "${TABLE}.rag_status",
            description="RAG from ewi_thresholds; NOT_ASSESSED where no threshold or value",
        ),
        dimension(
            "rag_rank",
            "number",
            "CASE ${TABLE}.rag_status WHEN 'RED' THEN 3 WHEN 'AMBER' THEN 2 "
            "WHEN 'GREEN' THEN 1 ELSE 0 END",
            label="Severity",
        ),
        dimension(
            "mom_change",
            "number",
            "${TABLE}.mom_change",
            description="Relative change against the previous month",
        ),
        measure(
            "value",
            "max",
            "${TABLE}.value",
            fmt="decimal_4",
            description="Pre-computed at month x country (independent path). Filter on one "
            "metric_id; do not aggregate across countries or months.",
        ),
        measure(
            "breaches",
            "count",
            None,
            filters={"rag_status": "AMBER,RED"},
            description="Month x country cells at AMBER or RED",
        ),
    ]
    for mid in monthly_metrics(pack):
        if mid not in NON_ADDITIVE:
            continue
        m = pack.metrics[mid]
        blocks.append(
            measure(
                mid,
                "max",
                f"CASE WHEN ${{TABLE}}.metric_id = '{mid}' THEN ${{TABLE}}.value END",
                fmt=VALUE_FORMAT[m.unit],
                description=description(m) + " (month x country only; not re-aggregatable)",
                tags=metric_tags(m),
            )
        )
    return view("ewi", "mart_ewi_monthly", blocks)


WORST_STATUS = (
    "CASE MAX(CASE ${TABLE}.status WHEN 'FAIL' THEN 2 WHEN 'WARN' THEN 1 ELSE 0 END) "
    "WHEN 2 THEN 'FAIL' WHEN 1 THEN 'WARN' ELSE 'PASS' END"
)


def view_dq_status(pack: DomainPack) -> str:
    blocks = [
        dimension(
            "pk",
            "string",
            "CONCAT(${TABLE}.run_id, '|', ${TABLE}.metric_id, '|', ${TABLE}.check_id, '|', "
            "CAST(${TABLE}.period_month AS STRING), '|', ${TABLE}.merchant_country)",
            pk=True,
            hidden=True,
        ),
        period_group("${TABLE}.period_month"),
        dimension("as_of", "date", "${TABLE}.as_of"),
        dimension("merchant_country", "string", "${TABLE}.merchant_country"),
        dimension("metric_id", "string", "${TABLE}.metric_id"),
        dimension("metric_version", "string", "${TABLE}.metric_version"),
        dimension("check_id", "string", "${TABLE}.check_id"),
        dimension("check_name", "string", "${TABLE}.check_name"),
        dimension(
            "principle_refs",
            "string",
            "ARRAY_TO_STRING(ARRAY(SELECT CAST(p AS STRING) FROM UNNEST(${TABLE}.principle_refs) "
            "AS p), ', ')",
            label="BCBS 239 principles",
        ),
        dimension("status", "string", "${TABLE}.status"),
        dimension("measured", "string", "${TABLE}.measured"),
        dimension("threshold", "string", "${TABLE}.threshold"),
        dimension("run_id", "string", "${TABLE}.run_id"),
        measure("count", "count", None),
        measure("fail_count", "count", None, filters={"status": "FAIL"}),
        measure("warn_count", "count", None, filters={"status": "WARN"}),
        measure("not_pass_count", "count", None, filters={"status": "-PASS"}),
        measure(
            "worst_status",
            "string",
            WORST_STATUS,
            description="Worst status in the cell (FAIL > WARN > PASS)",
        ),
    ]
    return view("dq_status", "mart_dq_status", blocks)


def view_bcbs239(pack: DomainPack) -> str:
    blocks = [
        dimension(
            "pk",
            "string",
            "CONCAT(CAST(${TABLE}.as_of AS STRING), '|', CAST(${TABLE}.principle_no AS STRING))",
            pk=True,
            hidden=True,
        ),
        dimension("as_of", "date", "${TABLE}.as_of"),
        dimension("principle_no", "number", "${TABLE}.principle_no"),
        dimension(
            "principle_name",
            "string",
            "CONCAT('P', CAST(${TABLE}.principle_no AS STRING), ' ', ${TABLE}.principle_name)",
        ),
        measure("checks_total", "sum", "${TABLE}.checks_total", fmt="decimal_0"),
        measure("pass", "sum", "${TABLE}.pass", fmt="decimal_0"),
        measure("warn", "sum", "${TABLE}.warn", fmt="decimal_0"),
        measure("fail", "sum", "${TABLE}.fail", fmt="decimal_0"),
        measure(
            "pass_rate",
            "number",
            "SAFE_DIVIDE(${pass}, ${checks_total})",
            fmt="percent_1",
            description="Share of check results (metric x check x month x country) that pass",
        ),
    ]
    return view("bcbs239", "mart_bcbs239_scorecard", blocks)


def view_timeliness(pack: DomainPack) -> str:
    blocks = [
        dimension(
            "pk",
            "string",
            "CONCAT(${TABLE}.table_name, '|', CAST(${TABLE}.partition_month AS STRING))",
            pk=True,
            hidden=True,
        ),
        dimension("as_of", "date", "${TABLE}.as_of"),
        dimension("table_name", "string", "${TABLE}.table_name"),
        period_group("${TABLE}.partition_month", name="partition"),
        dimension("expected_rows", "number", "${TABLE}.expected_rows"),
        dimension("loaded_rows", "number", "${TABLE}.loaded_rows"),
        dimension("completeness_pct", "number", "${TABLE}.completeness_pct"),
        time_group("last_loaded", "${TABLE}.last_loaded_at"),
        dimension("status", "string", "${TABLE}.status"),
        measure("count", "count", None),
        measure(
            "not_complete_count",
            "count",
            None,
            filters={"status": "-COMPLETE"},
            description="Partitions incomplete, partial or not loaded at the as-of date",
        ),
    ]
    return view("timeliness", "mart_timeliness", blocks)


def view_metric_catalog(pack: DomainPack) -> str:
    blocks = [
        dimension(
            "pk",
            "string",
            "CONCAT(${TABLE}.metric_id, '|', ${TABLE}.version)",
            pk=True,
            hidden=True,
        ),
        dimension("metric_id", "string", "${TABLE}.metric_id"),
        dimension("version", "string", "${TABLE}.version"),
        dimension("name", "string", "${TABLE}.name"),
        dimension("owner", "string", "${TABLE}.owner"),
        dimension("status", "string", "${TABLE}.status"),
        dimension("unit", "string", "${TABLE}.unit"),
        dimension("has_independent_path", "yesno", "${TABLE}.has_independent_path"),
        dimension("lineage_documented", "yesno", "${TABLE}.lineage_documented"),
        dimension("reconciles_to", "string", "${TABLE}.reconciles_to"),
        dimension("changed_at", "date", "${TABLE}.changed_at"),
        measure("count", "count", None),
        measure(
            "lineage_coverage",
            "number",
            "AVG(CASE WHEN ${TABLE}.has_independent_path AND ${TABLE}.lineage_documented "
            "THEN 1 ELSE 0 END)",
            fmt="percent_0",
            description="Share of metrics with an independent path and documented lineage",
        ),
    ]
    return view("metric_catalog", "mart_metric_catalog", blocks)


def view_requests(pack: DomainPack) -> str:
    blocks = [
        dimension("request_id", "string", "${TABLE}.request_id", pk=True),
        dimension("title", "string", "${TABLE}.title"),
        time_group("received", "${TABLE}.received_at"),
        dimension("deadline", "date", "${TABLE}.deadline"),
        time_group("delivered", "${TABLE}.delivered_at"),
        dimension("status", "string", "${TABLE}.status"),
        dimension("on_time", "yesno", "${TABLE}.on_time"),
        dimension("g2_escalations", "number", "${TABLE}.g2_escalations"),
        dimension("accepted_findings", "number", "${TABLE}.accepted_findings"),
        dimension("est_cost_eur", "number", "${TABLE}.est_cost_eur"),
        dimension("duration_s", "number", "${TABLE}.duration_s"),
        dimension("llm_replayed", "yesno", "${TABLE}.llm_replayed"),
        dimension("checks_pass", "number", "${TABLE}.checks_pass"),
        dimension("checks_warn", "number", "${TABLE}.checks_warn"),
        dimension("checks_fail", "number", "${TABLE}.checks_fail"),
        measure("count", "count", None),
        measure(
            "on_time_rate",
            "average",
            "CASE WHEN ${TABLE}.on_time THEN 1 WHEN NOT ${TABLE}.on_time THEN 0 END",
            fmt="percent_0",
            description="Delivered by the deadline, among requests that have one",
        ),
        measure("median_duration_hours", "median", "${TABLE}.duration_s / 3600", fmt="decimal_2"),
        measure("total_g2_escalations", "sum", "${TABLE}.g2_escalations", fmt="decimal_0"),
        measure("total_accepted_findings", "sum", "${TABLE}.accepted_findings", fmt="decimal_0"),
        measure(
            "avg_cost_eur",
            "average",
            "${TABLE}.est_cost_eur",
            fmt="eur_2",
            description="Estimated LLM cost per run (replayed runs are estimates; no API spend)",
        ),
    ]
    return view("requests", "mart_requests", blocks)


def view_gate_decisions(pack: DomainPack) -> str:
    blocks = [
        dimension("decision_id", "string", "${TABLE}.decision_id", pk=True),
        dimension("request_id", "string", "${TABLE}.request_id"),
        dimension("request_title", "string", "${TABLE}.request_title"),
        dimension("request_status", "string", "${TABLE}.request_status"),
        dimension("gate", "string", "${TABLE}.gate"),
        dimension("decision", "string", "${TABLE}.decision"),
        dimension("actor", "string", "${TABLE}.actor"),
        dimension("role", "string", "${TABLE}.role"),
        time_group("decided", "${TABLE}.ts"),
        dimension("acknowledged_warnings", "number", "${TABLE}.acknowledged_warnings"),
        measure("count", "count", None),
    ]
    return view("gate_decisions", "mart_gate_decisions", blocks)


VIEW_FILES = {
    "ewi_components": view_ewi_components,
    "ewi": view_ewi,
    "dq_status": view_dq_status,
    "bcbs239": view_bcbs239,
    "timeliness": view_timeliness,
    "metric_catalog": view_metric_catalog,
    "requests": view_requests,
    "gate_decisions": view_gate_decisions,
}


# ---- model and manifest --------------------------------------------------------------------
def manifest() -> str:
    return f"""project_name: {_q(MODEL)}

constant: DATASET_PREFIX {{
  value: "spb"
  export: override_optional
}}
"""


def model() -> str:
    formats = "".join(
        f"named_value_format: {n} {{\n  value_format: {_q(v)}\n}}\n\n"
        for n, v in NAMED_FORMATS.items()
    )
    formats += 'named_value_format: eur_2 {\n  value_format: "\\"€\\"#,##0.00"\n}\n\n'
    explores = []
    for name in ("ewi_components", "ewi", "dq_status", "bcbs239", "timeliness", "metric_catalog"):
        explores.append(f"explore: {name} {{\n  description: {_q(DISCLAIMER)}\n}}\n")
    explores.append(
        f"""explore: requests {{
  description: {_q(DISCLAIMER)}
  join: gate_decisions {{
    type: left_outer
    relationship: one_to_many
    sql_on: ${{requests.request_id}} = ${{gate_decisions.request_id}} ;;
  }}
}}
"""
    )
    return (
        f"connection: {_q(CONNECTION)}\n\n"
        'include: "/views/*.view.lkml"\n'
        'include: "/refinements/*.lkml"\n'
        'include: "/dashboards/*.dashboard.lookml"\n\n' + formats + "\n".join(explores)
    )


# ---- dashboards ------------------------------------------------------------------------------
def _filter(name: str, title: str, explore: str, field: str, default: str = "") -> dict[str, Any]:
    f: dict[str, Any] = {
        "name": name,
        "title": title,
        "type": "field_filter",
        "default_value": default,
        "allow_multiple_values": True,
        "required": False,
        "model": MODEL,
        "explore": explore,
        "field": field,
    }
    return f


def _el(
    name: str, title: str, explore: str, typ: str, fields: list[str], **kw: Any
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "name": name,
        "title": title,
        "model": MODEL,
        "explore": explore,
        "type": typ,
        "fields": fields,
    }
    e.update(kw)
    return e


def _text(
    name: str, body: str, row: int, col: int, width: int = 24, height: int = 2
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "text",
        "title_text": "",
        "body_text": body,
        "row": row,
        "col": col,
        "width": width,
        "height": height,
    }


def _pos(e: dict[str, Any], row: int, col: int, width: int, height: int) -> dict[str, Any]:
    return {**e, "row": row, "col": col, "width": width, "height": height}


def _rag_formatting(pack: DomainPack, metric_id: str) -> list[dict[str, Any]]:
    ewi = pack.metrics[metric_id].ewi
    if ewi is None:
        return []
    return [
        {
            "type": "greater than",
            "value": [ewi.red],
            "background_color": "#E8A0A0",
            "font_color": "#000000",
            "bold": True,
            "fields": None,
        },
        {
            "type": "between",
            "value": [ewi.amber, ewi.red],
            "background_color": "#F2D49B",
            "font_color": "#000000",
            "bold": False,
            "fields": None,
        },
    ]


def dashboard_risk_overview(pack: DomainPack) -> list[dict[str, Any]]:
    ec, ew = "ewi_components", "ewi"
    listen_ec = {
        "period": f"{ec}.period_month",
        "country": f"{ec}.merchant_country",
        "mcc": f"{ec}.mcc_group",
    }
    listen_ew = {"period": f"{ew}.period_month", "country": f"{ew}.merchant_country"}

    def latest(name: str, title: str, field: str, col: int) -> dict[str, Any]:
        return _pos(
            _el(
                name,
                title,
                ec,
                "single_value",
                [f"{ec}.period_month", f"{ec}.{field}"],
                sorts=[f"{ec}.period_month desc"],
                limit=2,
                show_comparison=True,
                comparison_type="change",
                comparison_label="vs previous month",
                listen=listen_ec,
            ),
            2,
            col,
            6,
            4,
        )

    return [
        {
            "dashboard": "risk_overview",
            "title": "Risk overview",
            "layout": "newspaper",
            "preferred_viewer": "dashboards-next",
            "description": "Independent early-warning indicators. " + DISCLAIMER,
            "filters": [
                _filter("period", "Period", ec, f"{ec}.period_month", "12 months"),
                _filter("country", "Merchant country", ec, f"{ec}.merchant_country"),
                _filter("mcc", "MCC group", ec, f"{ec}.mcc_group"),
            ],
            "elements": [
                _text(
                    "disclaimer",
                    DISCLAIMER + " Demonstration only. Figures computed by the oversight team "
                    "from raw sources (independent path). Hover a value for the metric "
                    "definition and version. Country RAG excludes XX (unknown country), which "
                    "is shown on Data risk.",
                    0,
                    0,
                ),
                latest(
                    "cb_rate_latest",
                    "Chargeback rate (value), latest month",
                    "chargeback_rate_value",
                    0,
                ),
                latest("fraud_latest", "Fraud loss rate, latest month", "fraud_loss_rate_bps", 6),
                latest("refund_latest", "Refund rate, latest month", "refund_rate_value", 12),
                _pos(
                    _el(
                        "top10_by_country",
                        "Top-10 merchant concentration by country, latest month",
                        ew,
                        "looker_grid",
                        [f"{ew}.merchant_country", f"{ew}.top10_merchant_concentration"],
                        filters={f"{ew}.period_month": "1 months ago for 1 months"},
                        sorts=[f"{ew}.top10_merchant_concentration desc"],
                        listen={"country": f"{ew}.merchant_country"},
                    ),
                    2,
                    18,
                    6,
                    4,
                ),
                _pos(
                    _el(
                        "cb_rate_heatmap",
                        "Chargeback rate (value) by country and month",
                        ew,
                        "looker_grid",
                        [f"{ew}.merchant_country", f"{ew}.period_month", f"{ew}.value"],
                        pivots=[f"{ew}.period_month"],
                        filters={f"{ew}.metric_id": "chargeback_rate_value"},
                        sorts=[f"{ew}.period_month", f"{ew}.merchant_country"],
                        value_format="0.00%",
                        conditional_formatting=_rag_formatting(pack, "chargeback_rate_value"),
                        listen=listen_ew,
                    ),
                    6,
                    0,
                    24,
                    6,
                ),
                _pos(
                    _el(
                        "volume_by_month",
                        "Settled volume by month and country",
                        ec,
                        "looker_line",
                        [
                            f"{ec}.period_month",
                            f"{ec}.merchant_country",
                            f"{ec}.settled_volume_eur",
                        ],
                        pivots=[f"{ec}.merchant_country"],
                        sorts=[f"{ec}.period_month"],
                        listen=listen_ec,
                    ),
                    12,
                    0,
                    12,
                    7,
                ),
                _pos(
                    _el(
                        "breaches",
                        "Threshold breaches",
                        ew,
                        "looker_grid",
                        [
                            f"{ew}.rag_rank",
                            f"{ew}.rag_status",
                            f"{ew}.metric_id",
                            f"{ew}.merchant_country",
                            f"{ew}.period_month",
                            f"{ew}.value",
                            f"{ew}.amber",
                            f"{ew}.red",
                        ],
                        filters={f"{ew}.rag_status": "AMBER,RED"},
                        sorts=[f"{ew}.rag_rank desc", f"{ew}.period_month desc"],
                        listen=listen_ew,
                    ),
                    12,
                    12,
                    12,
                    7,
                ),
            ],
        }
    ]


def dashboard_data_risk(pack: DomainPack) -> list[dict[str, Any]]:
    dq, ec, tl = "dq_status", "ewi_components", "timeliness"
    listen_dq = {"period": f"{dq}.period_month", "country": f"{dq}.merchant_country"}

    def single(
        name: str,
        title: str,
        explore: str,
        field: str,
        col: int,
        filters: dict[str, str] | None = None,
        listen: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {}
        if filters:
            kw["filters"] = filters
        if listen:
            kw["listen"] = listen
        return _pos(_el(name, title, explore, "single_value", [field], **kw), 2, col, 4, 4)

    xx = pack.pack.unknown_country_code
    return [
        {
            "dashboard": "data_risk",
            "title": "Data risk (BCBS 239)",
            "layout": "newspaper",
            "preferred_viewer": "dashboards-next",
            "description": "Can the data behind the indicators be trusted? " + DISCLAIMER,
            "filters": [
                _filter("period", "Period", dq, f"{dq}.period_month", "12 months"),
                _filter("country", "Merchant country", dq, f"{dq}.merchant_country"),
            ],
            "elements": [
                _text(
                    "disclaimer",
                    DISCLAIMER + " Demonstration only. Latest data-quality scan per as-of date.",
                    0,
                    0,
                ),
                single(
                    "rec_breaks",
                    "Open reconciliation breaks",
                    dq,
                    f"{dq}.fail_count",
                    0,
                    {f"{dq}.check_id": "V-REC-01"},
                    listen_dq,
                ),
                single(
                    "xx_volume",
                    f"Unallocated volume ({xx})",
                    ec,
                    f"{ec}.settled_volume_eur",
                    4,
                    {f"{ec}.merchant_country": xx},
                    {"period": f"{ec}.period_month"},
                ),
                single(
                    "completeness",
                    "Completeness issues",
                    dq,
                    f"{dq}.not_pass_count",
                    8,
                    {f"{dq}.check_id": "V-COMP-01,V-COMP-02"},
                    listen_dq,
                ),
                single(
                    "late_partitions",
                    "Incomplete or late partitions",
                    tl,
                    f"{tl}.not_complete_count",
                    12,
                    None,
                    {"period": f"{tl}.partition_month"},
                ),
                single(
                    "def_conformity",
                    "Definition conformity failures",
                    dq,
                    f"{dq}.fail_count",
                    16,
                    {f"{dq}.check_id": "V-DEF-01"},
                    listen_dq,
                ),
                single(
                    "lineage",
                    "Lineage coverage",
                    "metric_catalog",
                    "metric_catalog.lineage_coverage",
                    20,
                ),
                _pos(
                    _el(
                        "xx_by_month",
                        f"Unallocated volume ({xx}) by month",
                        ec,
                        "looker_column",
                        [f"{ec}.period_month", f"{ec}.settled_volume_eur"],
                        filters={f"{ec}.merchant_country": xx},
                        sorts=[f"{ec}.period_month"],
                        listen={"period": f"{ec}.period_month"},
                    ),
                    6,
                    0,
                    8,
                    6,
                ),
                _pos(
                    _el(
                        "check_matrix",
                        "Check status by month (worst status per cell)",
                        dq,
                        "looker_grid",
                        [f"{dq}.check_id", f"{dq}.period_month", f"{dq}.worst_status"],
                        pivots=[f"{dq}.period_month"],
                        sorts=[f"{dq}.period_month", f"{dq}.check_id"],
                        listen=listen_dq,
                    ),
                    6,
                    8,
                    16,
                    6,
                ),
                _pos(
                    _el(
                        "pass_rate_principle",
                        "Pass rate by BCBS 239 principle",
                        "bcbs239",
                        "looker_bar",
                        ["bcbs239.principle_name", "bcbs239.pass_rate"],
                        sorts=["bcbs239.principle_name"],
                    ),
                    12,
                    0,
                    8,
                    6,
                ),
                _pos(
                    _el(
                        "timeliness_table",
                        "Timeliness by table and month",
                        tl,
                        "looker_grid",
                        [
                            f"{tl}.table_name",
                            f"{tl}.partition_month",
                            f"{tl}.expected_rows",
                            f"{tl}.loaded_rows",
                            f"{tl}.completeness_pct",
                            f"{tl}.status",
                        ],
                        sorts=[f"{tl}.table_name", f"{tl}.partition_month desc"],
                        listen={"period": f"{tl}.partition_month"},
                    ),
                    12,
                    8,
                    16,
                    6,
                ),
                _pos(
                    _el(
                        "findings",
                        "Finding details",
                        dq,
                        "looker_grid",
                        [
                            f"{dq}.metric_id",
                            f"{dq}.check_id",
                            f"{dq}.period_month",
                            f"{dq}.merchant_country",
                            f"{dq}.status",
                            f"{dq}.measured",
                            f"{dq}.threshold",
                        ],
                        filters={f"{dq}.status": "-PASS"},
                        sorts=[f"{dq}.period_month desc", f"{dq}.check_id"],
                        listen=listen_dq,
                    ),
                    18,
                    0,
                    24,
                    8,
                ),
            ],
        }
    ]


def dashboard_request_operations(pack: DomainPack) -> list[dict[str, Any]]:
    rq, gd = "requests", "gate_decisions"
    listen = {"period": f"{rq}.received_date"}

    def single(name: str, title: str, field: str, col: int) -> dict[str, Any]:
        return _pos(_el(name, title, rq, "single_value", [field], listen=listen), 2, col, 4, 4)

    return [
        {
            "dashboard": "request_operations",
            "title": "Request operations",
            "layout": "newspaper",
            "preferred_viewer": "dashboards-next",
            "description": "How the oversight team handles supervisory requests. " + DISCLAIMER,
            "filters": [_filter("period", "Received", rq, f"{rq}.received_date", "")],
            "elements": [
                _text(
                    "disclaimer",
                    DISCLAIMER + " Demonstration only. Merchant country does "
                    "not apply to requests.",
                    0,
                    0,
                ),
                single("requests", "Requests", f"{rq}.count", 0),
                single("on_time", "Delivered on time", f"{rq}.on_time_rate", 4),
                single(
                    "median_hours",
                    "Median time to delivery (hours)",
                    f"{rq}.median_duration_hours",
                    8,
                ),
                single("g2", "G2 escalations", f"{rq}.total_g2_escalations", 12),
                single("accepted", "Accepted findings open", f"{rq}.total_accepted_findings", 16),
                single("cost", "Average cost per run", f"{rq}.avg_cost_eur", 20),
                _pos(
                    _el(
                        "request_table",
                        "Requests",
                        rq,
                        "looker_grid",
                        [
                            f"{rq}.request_id",
                            f"{rq}.title",
                            f"{rq}.status",
                            f"{rq}.deadline",
                            f"{rq}.delivered_time",
                            f"{rq}.g2_escalations",
                        ],
                        sorts=[f"{rq}.request_id desc"],
                        listen=listen,
                    ),
                    6,
                    0,
                    14,
                    7,
                ),
                _pos(
                    _el(
                        "gates",
                        "Gate decisions by gate and outcome",
                        rq,
                        "looker_bar",
                        [f"{gd}.gate", f"{gd}.decision", f"{gd}.count"],
                        pivots=[f"{gd}.decision"],
                        sorts=[f"{gd}.gate"],
                        listen=listen,
                    ),
                    6,
                    14,
                    10,
                    7,
                ),
            ],
        }
    ]


DASHBOARDS = {
    "risk_overview": dashboard_risk_overview,
    "data_risk": dashboard_data_risk,
    "request_operations": dashboard_request_operations,
}


# ---- files -----------------------------------------------------------------------------------
def with_header(pack: DomainPack, body: str) -> str:
    p = pack.pack
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return (
        f"# GENERATED FROM domain pack {p.id}@{p.version} — DO NOT EDIT\n"
        f"# content-sha256: {digest}\n" + body
    )


def body_of(text: str) -> tuple[str | None, str]:
    """(declared content hash, body) of a generated file."""
    lines = text.split("\n", 2)
    if len(lines) < 3 or not lines[0].startswith("# GENERATED FROM domain pack "):
        return None, text
    m = HASH_RE.match(lines[1])
    return (m.group(1) if m else None), lines[2]


def generate(pack: DomainPack) -> dict[str, str]:
    """Relative path under looker/ -> file content."""
    files = {"manifest.lkml": manifest(), f"{MODEL}.model.lkml": model()}
    for name, fn in VIEW_FILES.items():
        files[f"views/{name}.view.lkml"] = fn(pack)
    for name, dash in DASHBOARDS.items():
        files[f"dashboards/{name}.dashboard.lookml"] = yaml.safe_dump(
            dash(pack), sort_keys=False, allow_unicode=True, width=100
        )
    return {path: with_header(pack, body) for path, body in sorted(files.items())}
