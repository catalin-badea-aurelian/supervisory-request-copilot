"""Analyst agent: turn a confirmed spec into governed queries (ARCHITECTURE section 5).

The model proposes a `QueryPlan` from the spec and the metric definitions (definition view
only: no tolerances, checks, EWI thresholds or control tables). Code checks that the plan
matches the spec exactly, compiles each item from the glossary template and runs it in the
guarded metric-profile session on the run snapshot. A plan that departs from the spec, or asks
for non-template SQL, is rejected and escalated (free SQL is not part of M4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.contracts.analysis import AnalysisResult, QueryPlan, QueryRun, SourcePath
from copilot.contracts.request import RequestSpec
from copilot.data.hashing import canonical_hash
from copilot.llm.gateway import CallContext, Gateway, LlmEscalation
from copilot.packs.compiler import CompiledQuery, MetricRequest, compile_metric
from copilot.packs.models import DomainPack
from copilot.packs.views import metric_definition_view


class PlanRejected(LlmEscalation):
    pass


def check_plan(spec: RequestSpec, plan: QueryPlan) -> list[str]:
    """Differences between the plan and the confirmed spec (empty = acceptable)."""
    errors: list[str] = []
    planned = [i.metric_id for i in plan.items]
    if sorted(planned) != sorted(spec.metrics) or len(set(planned)) != len(planned):
        errors.append(f"plan metrics {planned} differ from the spec {spec.metrics}")
    for item in plan.items:
        if item.non_template:
            errors.append(f"{item.metric_id}: non-template SQL is not allowed in this milestone")
        if sorted(item.dimensions) != sorted(spec.dimensions):
            errors.append(
                f"{item.metric_id}: dimensions {item.dimensions} differ from {spec.dimensions}"
            )
        filters = {f.dimension: sorted(f.values) for f in item.filters}
        if filters != {k: sorted(v) for k, v in spec.filters.items()}:
            errors.append(f"{item.metric_id}: filters {filters} differ from {spec.filters}")
        if item.source_path != "preferred":
            errors.append(f"{item.metric_id}: the first run uses the preferred source path")
    return errors


def metric_request(spec: RequestSpec, metric_id: str) -> MetricRequest:
    """The governed request for one metric: always the spec's dimensions (period first)."""
    return MetricRequest(
        metric_id=metric_id,
        period_start=spec.period.start,
        period_end=spec.period.end,
        as_of=spec.as_of_date,
        grain=spec.period.grain,
        dims=tuple(spec.dimensions),
        filters={k: tuple(v) for k, v in spec.filters.items()},
    )


class AnalystAgent:
    prompt = "analyst"

    def __init__(self, gateway: Gateway, pack: DomainPack):
        self.gateway, self.pack = gateway, pack

    def inputs(self, spec: RequestSpec) -> dict[str, Any]:
        return {
            "spec": spec.model_dump(
                mode="json", include={"metrics", "dimensions", "filters", "period", "as_of_date"}
            ),
            "metric_definitions": [metric_definition_view(self.pack, m) for m in spec.metrics],
        }

    def plan(self, spec: RequestSpec, ctx: CallContext) -> QueryPlan:
        plan, _ = self.gateway.call(self.prompt, self.inputs(spec), QueryPlan, ctx)
        errors = check_plan(spec, plan)
        if errors:
            raise PlanRejected(
                "analyst", "the query plan does not match the spec", "; ".join(errors)
            )
        return plan


def execute_plan(
    pack: DomainPack,
    spec: RequestSpec,
    plan: QueryPlan,
    session: Any,
    run_dir: Path,
    source_path: SourcePath,
) -> tuple[AnalysisResult, list[tuple[CompiledQuery, Any]]]:
    """Compile from templates and run in the guarded session; results go to run_dir/results."""
    runs: list[QueryRun] = []
    compiled: list[tuple[CompiledQuery, Any]] = []
    out_dir = run_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(plan.items, key=lambda i: i.metric_id):
        req = metric_request(spec, item.metric_id)
        q = compile_metric(pack, req, source_path, session.dialect, session.resolver)
        df, record = session.backend.execute(q.sql, q.params)
        ref = f"results/{item.metric_id}__{source_path}.parquet"
        df.to_parquet(run_dir / ref, index=False)
        runs.append(
            QueryRun(
                metric_id=q.metric_id,
                metric_version=q.metric_version,
                template_id=q.template_id,
                non_template=False,
                rationale=item.rationale or None,
                source_path=source_path,
                sql=q.sql,
                sql_sha256=record.sql_sha256,
                params={k: v.isoformat() for k, v in sorted(q.params.items())},
                snapshot_id=session.snapshot_id,
                bytes_processed=record.bytes_processed,
                result_sha256=canonical_hash(df),
                rows=len(df),
                result_ref=ref,
            )
        )
        compiled.append((q, df))
    return AnalysisResult(request_id=spec.request_id, runs=runs, source_path=source_path), compiled
