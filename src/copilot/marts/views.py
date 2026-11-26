"""Generate the `spb_marts` views from the domain pack (DASHBOARD_SPEC section 2).

Metric marts compile the governed **independent** templates with `compile_metric`, so the
dashboard reads the oversight team's own figures through exactly the copilot's SQL. Query
parameters are inlined as date literals: the pack's full data range at its default as-of
date. Nothing in a mart restates metric logic.

- `mart_ewi_components`: additive components (sums and counts) at month x country x MCC
  group, including the unknown country (XX); ratios are LookML measures (SUM/SUM)
- `mart_ewi_monthly`: pre-computed values at month x country for the EWI metrics and the
  metrics that cannot be re-aggregated, with RAG from `ewi_thresholds`. XX is excluded: it
  is a data artefact, not a market, and Data risk shows its volume instead
- `mart_dq_status`, `mart_bcbs239_scorecard`: the latest scan per as-of from `spb_ops`
- `mart_timeliness`: the load log at the pack's default as-of date
- `mart_metric_catalog`, `mart_requests`, `mart_gate_decisions`: the `spb_ops` exports

`ref(layer, table)` returns the table reference. Committed files use the placeholders
`${project}` and `${prefix}`, substituted at deploy time.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from copilot.packs.compiler import MetricRequest, compile_metric
from copilot.packs.models import DomainPack

Ref = Callable[[str, str], str]

# Metrics whose value cannot be rebuilt by summing components across slices: a distinct count
# and a share of the top 10 merchants of each slice. They are served at month x country only.
NON_ADDITIVE = ("active_merchants", "top10_merchant_concentration")
# unit -> multiplier applied to SUM(numerator) / SUM(denominator), as in the templates
UNIT_MULTIPLIER = {"ratio": 1, "eur": 1, "per_mille": 1000, "bps": 10000}

COMPONENT_DIMS = ("period", "merchant_country", "mcc_group")
MONTHLY_DIMS = ("period", "merchant_country")

BCBS239_PRINCIPLES = {
    1: "Governance",
    2: "Data architecture and IT infrastructure",
    3: "Accuracy and integrity",
    4: "Completeness",
    5: "Timeliness",
    6: "Adaptability",
    7: "Accuracy (reporting)",
    8: "Comprehensiveness",
    9: "Clarity and usefulness",
    10: "Frequency",
    11: "Distribution",
}

PLACEHOLDER_REF: Ref = lambda layer, table: f"`${{project}}.${{prefix}}_{layer}.{table}`"  # noqa: E731


def header(pack: DomainPack, comment: str = "--") -> str:
    p = pack.pack
    return f"{comment} GENERATED FROM domain pack {p.id}@{p.version} — DO NOT EDIT"


def _date(d: date) -> str:
    return f"DATE '{d.isoformat()}'"


def _inline(sql: str, params: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            raise ValueError(f"unknown parameter {name}")
        return _date(params[name])

    return re.sub(r"[@$]([a-z_]+)\b", sub, sql)


def metric_sql(
    pack: DomainPack, metric_id: str, dims: tuple[str, ...], ref: Ref, dialect: str = "bigquery"
) -> str:
    """The governed independent query for the full data range, parameters inlined."""
    p = pack.pack
    req = MetricRequest(
        metric_id, p.data_range["start"], p.data_range["end"], p.default_as_of, "month", dims
    )

    def resolve(name: str) -> str:
        return ref(p.sources[name].layer, name)

    q = compile_metric(pack, req, "independent", dialect, resolve)  # type: ignore[arg-type]
    return _inline(q.sql, q.params)


# ---- components -----------------------------------------------------------------------------
def components(pack: DomainPack) -> dict[str, str]:
    """Additive component -> the metric whose independent template produces it.

    A component is an additive metric itself (EUR amount or count without a numerator), or
    the numerator or denominator of a decomposable metric.
    """
    out: dict[str, str] = {}
    for mid, m in sorted(pack.metrics.items()):
        if mid in NON_ADDITIVE or m.status != "approved":
            continue
        if m.numerator is None:
            out.setdefault(mid, mid)
    for mid, m in sorted(pack.metrics.items()):
        if mid in NON_ADDITIVE or m.status != "approved" or m.numerator is None:
            continue
        for comp in (m.numerator, m.denominator):
            if comp is not None:
                out.setdefault(comp, mid)
    return dict(sorted(out.items(), key=lambda kv: _component_order(pack, kv[0])))


def _component_order(pack: DomainPack, name: str) -> tuple[int, str]:
    return (0 if name in pack.metrics else 1, name)


def component_type(name: str) -> str:
    return "INT64" if name.endswith("_count") else "NUMERIC"


def mart_ewi_components(pack: DomainPack, ref: Ref, dialect: str = "bigquery") -> str:
    comps = components(pack)
    branches = []
    for metric_id in sorted(set(comps.values())):
        provided = [c for c, src in comps.items() if src == metric_id]
        cols = ", ".join(
            f"CAST({c} AS {component_type(c)}) AS {c}"
            if c in provided
            else f"CAST(NULL AS {component_type(c)}) AS {c}"
            for c in comps
        )
        inner = metric_sql(pack, metric_id, COMPONENT_DIMS, ref, dialect)
        branches.append(
            f"  SELECT period AS period_month, merchant_country, mcc_group, {cols}\n"
            f"  FROM (\n{inner}\n  ) AS m_{metric_id}"
        )
    sums = ",\n  ".join(f"COALESCE(SUM({c}), 0) AS {c}" for c in comps)
    return (
        "SELECT period_month, merchant_country, mcc_group,\n  "
        + sums
        + "\nFROM (\n"
        + "\n  UNION ALL\n".join(branches)
        + "\n) AS components\nGROUP BY period_month, merchant_country, mcc_group"
    )


# ---- monthly values with RAG ----------------------------------------------------------------
def monthly_metrics(pack: DomainPack) -> list[str]:
    return sorted(
        mid
        for mid, m in pack.metrics.items()
        if m.status == "approved" and (m.ewi is not None or mid in NON_ADDITIVE)
    )


def mart_ewi_monthly(pack: DomainPack, ref: Ref, dialect: str = "bigquery") -> str:
    unknown = pack.pack.unknown_country_code
    branches = []
    for mid in monthly_metrics(pack):
        m = pack.metrics[mid]
        branches.append(
            f"  SELECT period AS period_month, merchant_country, '{mid}' AS metric_id, "
            f"'{m.version}' AS metric_version, CAST({mid} AS FLOAT64) AS value\n"
            f"  FROM (\n{metric_sql(pack, mid, MONTHLY_DIMS, ref, dialect)}\n  ) AS m_{mid}"
        )
    union = "\n  UNION ALL\n".join(branches)
    return f"""WITH v AS (
{union}
),
scored AS (
  SELECT v.period_month, v.merchant_country, v.metric_id, v.metric_version, v.value,
    t.amber, t.red, t.direction,
    CASE
      WHEN t.metric_id IS NULL OR v.value IS NULL THEN 'NOT_ASSESSED'
      WHEN t.direction = 'higher_is_worse' AND v.value >= t.red THEN 'RED'
      WHEN t.direction = 'higher_is_worse' AND v.value >= t.amber THEN 'AMBER'
      WHEN t.direction = 'lower_is_worse' AND v.value <= t.red THEN 'RED'
      WHEN t.direction = 'lower_is_worse' AND v.value <= t.amber THEN 'AMBER'
      ELSE 'GREEN'
    END AS rag_status,
    SAFE_DIVIDE(
      v.value - LAG(v.value) OVER (
        PARTITION BY v.metric_id, v.merchant_country ORDER BY v.period_month),
      LAG(v.value) OVER (PARTITION BY v.metric_id, v.merchant_country ORDER BY v.period_month)
    ) AS mom_change
  FROM v
  LEFT JOIN {ref("control", "ewi_thresholds")} AS t ON t.metric_id = v.metric_id
  WHERE v.merchant_country <> '{unknown}'
)
SELECT * FROM scored"""


# ---- data risk and operations ---------------------------------------------------------------
def _latest_scan(ref: Ref) -> str:
    return f"""SELECT * FROM {ref("ops", "ops_check_results")}
  WHERE run_type = 'scan'
  QUALIFY DENSE_RANK() OVER (PARTITION BY as_of ORDER BY recorded_at DESC, run_id DESC) = 1"""


def mart_dq_status(pack: DomainPack, ref: Ref) -> str:
    return f"""WITH latest AS (
  {_latest_scan(ref)}
)
SELECT as_of, period_month, merchant_country, metric_id, metric_version, check_id, check_name,
  principle_refs, status, measured, threshold, run_type, run_id, pack_version
FROM latest"""


def mart_bcbs239_scorecard(pack: DomainPack, ref: Ref) -> str:
    names = "\n    ".join(f"WHEN {n} THEN '{t}'" for n, t in BCBS239_PRINCIPLES.items())
    return f"""WITH latest AS (
  {_latest_scan(ref)}
),
by_principle AS (
  SELECT as_of, principle_no, status
  FROM latest, UNNEST(principle_refs) AS principle_no
)
SELECT as_of, principle_no,
  CASE principle_no
    {names}
  END AS principle_name,
  COUNT(*) AS checks_total,
  COUNTIF(status = 'PASS') AS pass,
  COUNTIF(status = 'WARN') AS warn,
  COUNTIF(status = 'FAIL') AS fail,
  SAFE_DIVIDE(COUNTIF(status = 'PASS'), COUNT(*)) AS pass_rate
FROM by_principle
GROUP BY as_of, principle_no"""


def mart_timeliness(pack: DomainPack, ref: Ref) -> str:
    t = {**pack.pack.defaults.get("V-TIME-01", {})}
    warn, fail = float(t["warn"]), float(t["fail"])
    as_of = _date(pack.pack.default_as_of)
    return f"""WITH log AS (
  SELECT table_name, PARSE_DATE('%Y-%m', `partition`) AS partition_month,
    MAX(expected_row_count) AS expected_rows,
    SUM(IF(DATE(loaded_at) <= {as_of}, row_count, 0)) AS loaded_rows,
    MAX(IF(DATE(loaded_at) <= {as_of}, loaded_at, NULL)) AS last_loaded_at
  FROM {ref("raw", "meta_load_log")}
  GROUP BY table_name, `partition`
)
SELECT {as_of} AS as_of, table_name, partition_month, expected_rows, loaded_rows,
  SAFE_DIVIDE(loaded_rows, expected_rows) AS completeness_pct, last_loaded_at,
  CASE
    WHEN loaded_rows = 0 THEN 'NOT_LOADED'
    WHEN SAFE_DIVIDE(loaded_rows, expected_rows) < {fail} THEN 'INCOMPLETE'
    WHEN SAFE_DIVIDE(loaded_rows, expected_rows) < {warn} THEN 'PARTIAL'
    ELSE 'COMPLETE'
  END AS status
FROM log"""


def mart_metric_catalog(pack: DomainPack, ref: Ref) -> str:
    return f"""SELECT metric_id, version, name, owner, status, unit, has_independent_path,
  lineage_documented, reconciles_to, changed_at, pack_version
FROM {ref("ops", "ops_metric_catalog")}"""


def mart_requests(pack: DomainPack, ref: Ref) -> str:
    """One row per request run, with the worst status of each of its checks counted."""
    return f"""WITH per_check AS (
  SELECT run_id, metric_id, check_id,
    CASE MAX(CASE status WHEN 'FAIL' THEN 3 WHEN 'WARN' THEN 2 ELSE 1 END)
      WHEN 3 THEN 'FAIL' WHEN 2 THEN 'WARN' ELSE 'PASS' END AS status
  FROM {ref("ops", "ops_check_results")}
  WHERE run_type = 'request'
  GROUP BY run_id, metric_id, check_id
),
counts AS (
  SELECT run_id,
    COUNTIF(status = 'PASS') AS checks_pass,
    COUNTIF(status = 'WARN') AS checks_warn,
    COUNTIF(status = 'FAIL') AS checks_fail
  FROM per_check
  GROUP BY run_id
)
SELECT r.request_id, r.title, r.received_at, r.deadline, r.delivered_at, r.status,
  CASE
    WHEN r.deadline IS NULL THEN NULL
    WHEN r.delivered_at IS NULL THEN FALSE
    ELSE DATE(r.delivered_at) <= r.deadline
  END AS on_time,
  r.g2_escalations, r.accepted_findings, r.est_cost_eur, r.duration_s, r.llm_calls,
  r.llm_replayed, r.tokens_in, r.tokens_out, r.bytes_processed, r.backend, r.pack_version,
  COALESCE(c.checks_pass, 0) AS checks_pass,
  COALESCE(c.checks_warn, 0) AS checks_warn,
  COALESCE(c.checks_fail, 0) AS checks_fail
FROM {ref("ops", "ops_runs")} AS r
LEFT JOIN counts AS c ON c.run_id = r.request_id"""


def mart_gate_decisions(pack: DomainPack, ref: Ref) -> str:
    """One row per gate decision, with the request's title and status for filtering."""
    return f"""SELECT g.request_id, r.title AS request_title, r.status AS request_status,
  g.decision_id, g.gate, g.decision, g.actor, g.role, g.ts,
  ARRAY_LENGTH(g.acknowledged_warnings) AS acknowledged_warnings
FROM {ref("ops", "ops_gate_decisions")} AS g
LEFT JOIN {ref("ops", "ops_runs")} AS r ON r.request_id = g.request_id"""


VIEWS: dict[str, Callable[..., str]] = {
    "mart_ewi_components": mart_ewi_components,
    "mart_ewi_monthly": mart_ewi_monthly,
    "mart_dq_status": mart_dq_status,
    "mart_bcbs239_scorecard": mart_bcbs239_scorecard,
    "mart_timeliness": mart_timeliness,
    "mart_metric_catalog": mart_metric_catalog,
    "mart_requests": mart_requests,
    "mart_gate_decisions": mart_gate_decisions,
}
METRIC_VIEWS = ("mart_ewi_components", "mart_ewi_monthly")


def generate(pack: DomainPack, ref: Ref = PLACEHOLDER_REF) -> dict[str, str]:
    """View name -> BigQuery SELECT (without header)."""
    return {name: fn(pack, ref) for name, fn in VIEWS.items()}


def render_file(pack: DomainPack, name: str, sql: str) -> str:
    return f"{header(pack)}\n-- view: {name}\n{sql.rstrip()}\n"


def substitute(sql: str, project: str, prefix: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,29}", project or ""):
        raise ValueError("GCP_PROJECT_ID is missing or not a project id")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", prefix or ""):
        raise ValueError("BQ_DATASET_PREFIX is not a valid dataset prefix")
    return sql.replace("${project}", project).replace("${prefix}", prefix)
