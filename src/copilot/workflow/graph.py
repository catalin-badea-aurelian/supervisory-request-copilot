"""The workflow graph (ARCHITECTURE section 4): deterministic code, LLMs only inside nodes.

    intake ⇄ ask ─► gate_1 ─► analyst ─► validation ─► (new FAIL) gate_2 ─► analyst …
                                              └─► review (M5 seam) ─► evidence ─► gate_3 ─► end
    intake ─► metric_proposal (M6 seam, ON_HOLD)        any agent escalation ─► gate_error

Human gates are LangGraph interrupts; a run pauses there and is resumed with the decision
(`Command(resume=...)`). State is JSON only (Pydantic models dumped), so the SQLite checkpoint
does not depend on pickled classes. A node that interrupts is re-executed on resume, so gate
nodes build their payload deterministically and write to the audit log only after the
decision arrives. Every transition is decided here; a model output is only ever an input.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from copilot.agents.analyst import AnalystAgent, execute_plan, metric_request
from copilot.agents.explainer import explain_failure
from copilot.agents.intake import MAX_TURNS, IntakeAgent
from copilot.agents.precedent import Submission, load_submissions, past_runs
from copilot.audit.log import AuditLog, canonical_json, sha256_text
from copilot.audit.manifest import RunManifest, write_manifest
from copilot.config import Settings
from copilot.contracts.analysis import AnalysisResult, QueryPlan, QueryRun
from copilot.contracts.gates import (
    G1_OPTIONS,
    G2_ERROR_OPTIONS,
    G2_OPTIONS,
    G3_OPTIONS,
    AcceptedFinding,
    GateDecision,
    GatePayload,
)
from copilot.contracts.request import QAPair, Question, RequestSpec
from copilot.contracts.validation import ValidationReport
from copilot.data.session import open_session
from copilot.data.snapshots import snapshot_from_manifest
from copilot.evidence.minimal import build_delivery
from copilot.llm.gateway import BudgetExceeded, CallContext, Gateway, LlmEscalation, RunBudget
from copilot.packs.models import DomainPack
from copilot.validation.runner import run_checks
from copilot.validation.target import ValidationTarget
from copilot.workflow.findings import accept, apply_accepted, new_fails, warning_ids


class RunState(TypedDict, total=False):
    request_id: str
    raw_request: str
    requester: str
    status: str  # IN_PROGRESS | ON_HOLD | REJECTED | DELIVERED
    status_reason: str
    submissions: list[dict[str, Any]]
    qa: list[dict[str, Any]]
    pending_questions: list[dict[str, Any]]
    intake_turns: int
    feedback: list[str]
    spec: dict[str, Any] | None
    plan: dict[str, Any] | None
    source_path: str
    snapshot: dict[str, Any] | None
    analysis: dict[str, Any] | None
    report: dict[str, Any] | None
    explanations: dict[str, Any]
    accepted: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    escalation: dict[str, Any] | None
    review: dict[str, Any] | None
    budget: dict[str, Any]
    next: str


SessionOpener = Callable[..., Any]


@dataclass
class Runtime:
    settings: Settings
    pack: DomainPack
    gateway: Gateway
    open_session: SessionOpener = open_session
    actor_human: str = "human"

    def run_dir(self, request_id: str) -> Path:
        return self.settings.runs_dir / request_id


# ---- helpers -------------------------------------------------------------------------------
def _budget(rt: Runtime, state: RunState) -> RunBudget:
    b = state.get("budget") or {}
    return RunBudget(limit_tokens=rt.settings.run_token_budget, **b)


def _budget_dump(b: RunBudget) -> dict[str, Any]:
    return {
        "spent_tokens": b.spent_tokens,
        "calls": b.calls,
        "cost_usd": round(b.cost_usd, 6),
        "tokens_in": b.tokens_in,
        "tokens_out": b.tokens_out,
    }


def _audit(rt: Runtime, state: RunState) -> AuditLog:
    rid = state["request_id"]
    return AuditLog(rt.run_dir(rid), rid)


def _ctx(rt: Runtime, state: RunState, actor: str) -> tuple[CallContext, RunBudget]:
    b = _budget(rt, state)
    return CallContext(budget=b, audit=_audit(rt, state), actor=actor), b


def _escalate(phase: str, node: str, e: LlmEscalation) -> dict[str, Any]:
    kind = "budget" if isinstance(e, BudgetExceeded) else "agent_error"
    return {
        "gate": "G1" if phase == "intake" else "G2",
        "kind": kind,
        "node": node,
        "reason": e.reason,
        "detail": e.detail,
        "prompt": e.prompt_ref,
    }


def _decision(state: RunState, gate: str, raw: Any, actor: str) -> GateDecision:
    raw = raw if isinstance(raw, dict) else {"decision": str(raw)}
    n = sum(1 for d in state.get("decisions", []) if d["gate"] == gate) + 1
    return GateDecision(
        decision_id=f"{state['request_id']}:{gate}:{n}",
        gate=gate,  # type: ignore[arg-type]
        decision=str(raw.get("decision", "")),
        actor=str(raw.get("actor", actor)),
        role=str(raw.get("role", "approver" if gate != "G1" else "requester")),
        comment=raw.get("comment"),
        acknowledged_warnings=sorted(raw.get("acknowledged_warnings", [])),
        ts=datetime.now(UTC).replace(microsecond=0),
    )


def _log_decision(rt: Runtime, state: RunState, d: GateDecision) -> list[dict[str, Any]]:
    _audit(rt, state).append(
        "gate_decision",
        actor=d.actor,
        role=d.role,
        summary=f"{d.gate}: {d.decision}",
        payload=d.model_dump(mode="json"),
    )
    return [*state.get("decisions", []), d.model_dump(mode="json")]


def _status(rt: Runtime, state: RunState, status: str, reason: str) -> dict[str, Any]:
    _audit(rt, state).append(
        "status_changed",
        actor="workflow",
        role="system",
        summary=f"{status}: {reason}",
        payload={"status": status, "reason": reason},
    )
    return {"status": status, "status_reason": reason}


# ---- nodes ---------------------------------------------------------------------------------
def build_graph(rt: Runtime, checkpointer: Any = None) -> Any:
    pack = rt.pack

    def intake(state: RunState) -> dict[str, Any]:
        audit = _audit(rt, state)
        update: dict[str, Any] = {}
        subs = state.get("submissions")
        if subs is None:
            audit.append(
                "request_received",
                actor=state["requester"],
                role="requester",
                summary=state["raw_request"][:120],
                payload={"request": state["raw_request"]},
            )
            session = rt.open_session(
                rt.settings,
                pack,
                request_id=state["request_id"],
                component="precedent",
                profile="validation",
                snapshot_mode="live",
            )
            try:
                found = load_submissions(session)
            finally:
                session.close()
            subs = [s.__dict__ for s in found]
            audit.append(
                "tool_call",
                actor="workflow",
                role="system",
                summary=f"precedent_search: {len(subs)} earlier submissions (metadata only)",
                payload={"tool": "precedent_search", "submissions": subs},
            )
            update.update(
                submissions=subs,
                status="IN_PROGRESS",
                qa=[],
                intake_turns=0,
                feedback=[],
                accepted=[],
                decisions=[],
                explanations={},
                source_path="preferred",
                budget={},
            )
            state = {**state, **update}  # type: ignore[typeddict-item]
        turns = state.get("intake_turns", 0) + 1
        if turns > MAX_TURNS:
            return {
                **update,
                "escalation": {
                    "gate": "G1",
                    "kind": "agent_error",
                    "node": "intake",
                    "reason": f"specification still open after {MAX_TURNS} rounds",
                    "detail": "",
                    "prompt": "intake",
                },
                "next": "gate_error",
            }
        agent = IntakeAgent(rt.gateway, pack, [Submission(**s) for s in subs])
        ctx, budget = _ctx(rt, state, "intake-agent")
        qa = [QAPair(**p) for p in state.get("qa", [])]
        try:
            out = agent.step(
                state["raw_request"],
                qa,
                ctx,
                request_id=state["request_id"],
                requester=state["requester"],
                feedback=state.get("feedback", []),
            )
        except LlmEscalation as e:
            return {
                **update,
                "budget": _budget_dump(budget),
                "intake_turns": turns,
                "escalation": _escalate("intake", "intake", e),
                "next": "gate_error",
            }
        update.update(budget=_budget_dump(budget), intake_turns=turns)
        if out.undefined_measures:
            return {
                **update,
                "next": "metric_proposal",
                "escalation": {"undefined_measures": out.undefined_measures},
            }
        if out.questions:
            for q in out.questions:
                audit.append(
                    "question_asked",
                    actor="intake-agent",
                    role="maker",
                    summary=f"[{q.topic}] {q.text}",
                    payload=q.model_dump(mode="json"),
                )
            return {
                **update,
                "pending_questions": [q.model_dump() for q in out.questions],
                "next": "ask",
            }
        assert out.spec is not None
        spec_json = out.spec.model_dump(mode="json")
        audit.append(
            "spec_drafted",
            actor="intake-agent",
            role="maker",
            summary=out.spec.title,
            payload=spec_json,
        )
        return {**update, "spec": spec_json, "pending_questions": [], "next": "gate_1"}

    def ask(state: RunState) -> dict[str, Any]:
        questions = [Question(**q) for q in state["pending_questions"]]
        answers = interrupt({"type": "questions", "questions": state["pending_questions"]})
        by_topic = {a["topic"]: a for a in answers} if isinstance(answers, list) else answers
        audit = _audit(rt, state)
        qa = list(state.get("qa", []))
        for q in questions:
            a = by_topic.get(q.topic)
            if a is None:
                continue
            text = a["answer"] if isinstance(a, dict) else str(a)
            actor = (
                a.get("actor", state["requester"]) if isinstance(a, dict) else state["requester"]
            )
            pair = QAPair(topic=q.topic, question=q.text, answer=text, actor=actor)
            audit.append(
                "answer_received",
                actor=actor,
                role="requester",
                summary=f"[{q.topic}] {text[:100]}",
                payload=pair.model_dump(mode="json"),
            )
            qa.append(pair.model_dump(mode="json"))
        return {"qa": qa, "pending_questions": []}

    def gate_1(state: RunState) -> dict[str, Any]:
        spec = state["spec"] or {}
        payload = GatePayload(
            gate="G1",
            kind="spec",
            summary=f"Confirm the specification: {spec.get('title')}",
            options=list(G1_OPTIONS),
            details={
                "spec": spec,
                "past_runs": past_runs(
                    rt.settings.runs_dir, spec.get("metrics", []), state["request_id"]
                ),
            },
        )
        raw = interrupt({"type": "gate", **payload.model_dump(mode="json")})
        d = _decision(state, "G1", raw, state["requester"])
        decisions = _log_decision(rt, state, d)
        if d.decision == "approve":
            run_dir = rt.run_dir(state["request_id"])
            (run_dir / "spec.json").write_text(canonical_json(spec), encoding="utf-8")
            return {"decisions": decisions, "next": "analyst"}
        if d.decision == "request_changes":
            return {
                "decisions": decisions,
                "spec": None,
                "next": "intake",
                "feedback": [*state.get("feedback", []), d.comment or "changes requested"],
            }
        return {"decisions": decisions, "next": "gate_1"}  # unknown option: ask again

    def analyst(state: RunState) -> dict[str, Any]:
        spec = RequestSpec(**state["spec"])  # type: ignore[arg-type]
        audit = _audit(rt, state)
        update: dict[str, Any] = {}
        plan_json = state.get("plan")
        budget = _budget(rt, state)
        if plan_json is None:
            ctx, budget = _ctx(rt, state, "analyst-agent")
            try:
                plan = AnalystAgent(rt.gateway, pack).plan(spec, ctx)
            except LlmEscalation as e:
                return {
                    "budget": _budget_dump(budget),
                    "next": "gate_error",
                    "escalation": _escalate("analysis", "analyst", e),
                }
            plan_json = plan.model_dump(mode="json")
            update.update(plan=plan_json, budget=_budget_dump(budget))
        plan = QueryPlan(**plan_json)
        snap_m = state.get("snapshot")
        session = rt.open_session(
            rt.settings,
            pack,
            request_id=state["request_id"],
            component="analyst",
            profile="metric",
            snapshot=snapshot_from_manifest(snap_m) if snap_m else None,
        )
        try:
            if snap_m is None:
                snap_m = session.snapshot.to_manifest()
                audit.append(
                    "snapshot_taken",
                    actor="workflow",
                    role="system",
                    summary=session.snapshot_id,
                    payload=snap_m,
                )
                update["snapshot"] = snap_m
            analysis, _ = execute_plan(
                pack,
                spec,
                plan,
                session,
                rt.run_dir(spec.request_id),
                state.get("source_path", "preferred"),  # type: ignore[arg-type]
            )
        finally:
            session.close()
        for run in analysis.runs:
            audit.append(
                "query_executed",
                actor="analyst-agent",
                role="maker",
                summary=f"{run.template_id} rows={run.rows}",
                payload=run.model_dump(mode="json", exclude={"sql"}) | {"sql": run.sql},
            )
        return {**update, "analysis": analysis.model_dump(mode="json"), "next": "validation"}

    def validation(state: RunState) -> dict[str, Any]:
        spec = RequestSpec(**state["spec"])  # type: ignore[arg-type]
        analysis = AnalysisResult(**state["analysis"])  # type: ignore[arg-type]
        report = validate_analysis(
            rt,
            spec,
            analysis,
            state["snapshot"],  # type: ignore[arg-type]
            [AcceptedFinding(**a) for a in state.get("accepted", [])],
        )
        audit = _audit(rt, state)
        for c in report.checks:
            audit.append(
                "check_result",
                actor="validation-engine",
                role="checker",
                summary=f"{c.metric_id} {c.check_id} {c.status}"
                + (f" (accepted {c.accepted_ref})" if c.accepted_ref else ""),
                payload=c.model_dump(mode="json"),
            )
        audit.append(
            "validation_completed",
            actor="validation-engine",
            role="checker",
            summary=f"{report.status} report={report.report_sha256[:12]}",
            payload={"report_sha256": report.report_sha256, "status": report.status},
        )
        update: dict[str, Any] = {"report": report.model_dump(mode="json")}
        fails = new_fails(report)
        if not fails:
            return {**update, "next": "review"}
        ctx, budget = _ctx(rt, state, "failure-explainer")
        explanations = dict(state.get("explanations", {}))
        for c in fails:
            key = f"{c.metric_id}:{c.check_id}"
            try:
                explanations[key] = explain_failure(rt.gateway, c, ctx).model_dump()
            except BudgetExceeded as e:
                return {
                    **update,
                    "explanations": explanations,
                    "budget": _budget_dump(budget),
                    "escalation": _escalate("analysis", "validation", e),
                    "next": "gate_error",
                }
            except LlmEscalation:
                explanations[key] = {
                    "summary": "No explanation available.",
                    "likely_cause": "",
                    "impact": "",
                }
        return {
            **update,
            "explanations": explanations,
            "budget": _budget_dump(budget),
            "next": "gate_2",
        }

    def gate_2(state: RunState) -> dict[str, Any]:
        report = ValidationReport(**state["report"])  # type: ignore[arg-type]
        fails = new_fails(report)
        independent = state.get("source_path") == "independent"
        options = [o for o in G2_OPTIONS if not (independent and o == "independent_path")]
        payload = GatePayload(
            gate="G2",
            kind="escalation",
            summary=f"{len(fails)} check(s) failed: "
            + ", ".join(sorted({f"{c.metric_id}:{c.check_id}" for c in fails})),
            options=options,
            details={
                "fails": [c.model_dump(mode="json") for c in fails],
                "explanations": state.get("explanations", {}),
                "explanation_label": "written by a language model; statuses are set by code",
            },
        )
        raw = interrupt({"type": "gate", **payload.model_dump(mode="json")})
        d = _decision(state, "G2", raw, rt.actor_human)
        decisions = _log_decision(rt, state, d)
        if d.decision == "independent_path" and not independent:
            accepted = [
                *state.get("accepted", []),
                *[a.model_dump() for a in accept(fails, d.decision_id)],
            ]
            return {
                "decisions": decisions,
                "accepted": accepted,
                "source_path": "independent",
                "next": "analyst",
            }
        if d.decision == "hold":
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "ON_HOLD", "held at G2: issue raised to the data owner"),
            }
        if d.decision == "reject":
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "REJECTED", "rejected at G2"),
            }
        return {"decisions": decisions, "next": "gate_2"}

    def gate_error(state: RunState) -> dict[str, Any]:
        esc = state["escalation"] or {}
        options = [
            o for o in G2_ERROR_OPTIONS if not (esc.get("kind") == "budget" and o == "retry")
        ]
        payload = GatePayload(
            gate=esc.get("gate", "G2"),
            kind=esc.get("kind", "agent_error"),
            summary=f"{esc.get('node')}: {esc.get('reason')}",
            options=options,
            details=esc,
        )
        raw = interrupt({"type": "gate", **payload.model_dump(mode="json")})
        d = _decision(state, payload.gate, raw, rt.actor_human)
        decisions = _log_decision(rt, state, d)
        if d.decision == "retry" and "retry" in options:
            return {
                "decisions": decisions,
                "escalation": None,
                "next": esc.get("node", "intake"),
                "intake_turns": 0,
            }
        if d.decision == "hold":
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "ON_HOLD", f"held after escalation: {esc.get('reason')}"),
            }
        if d.decision == "reject":
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "REJECTED", f"rejected after escalation: {esc.get('reason')}"),
            }
        return {"decisions": decisions, "next": "gate_error"}

    def metric_proposal(state: RunState) -> dict[str, Any]:
        # M6 seam: the GD metric-proposal flow. Never improvise a definition.
        measures = (state.get("escalation") or {}).get("undefined_measures", [])
        return {
            "next": "end",
            **_status(
                rt,
                state,
                "ON_HOLD",
                f"undefined metric {measures}: the metric-proposal flow arrives in M6",
            ),
        }

    def review(state: RunState) -> dict[str, Any]:
        # M5 seam: the BCBS 239 reviewer (RAG with citations) replaces this pass-through.
        return {
            "review": {
                "status": "not assessed",
                "note": "the BCBS 239 review is added in milestone M5",
            },
            "next": "evidence",
        }

    def evidence(state: RunState) -> dict[str, Any]:
        spec = RequestSpec(**state["spec"])  # type: ignore[arg-type]
        path = build_delivery(
            rt.run_dir(spec.request_id),
            spec,
            AnalysisResult(**state["analysis"]),  # type: ignore[arg-type]
            ValidationReport(**state["report"]),  # type: ignore[arg-type]
            [AcceptedFinding(**a) for a in state.get("accepted", [])],
            state.get("explanations", {}),
            state.get("review") or {},
        )
        text = path.read_text(encoding="utf-8")
        _audit(rt, state).append(
            "evidence_built",
            actor="workflow",
            role="system",
            summary=f"{path.name} sha256={sha256_text(text)[:12]}",
            payload={"file": path.name, "sha256": sha256_text(text)},
        )
        _write_manifest(rt, state, "IN_PROGRESS")
        return {"next": "gate_3"}

    def gate_3(state: RunState) -> dict[str, Any]:
        report = ValidationReport(**state["report"])  # type: ignore[arg-type]
        required = warning_ids(report)
        payload = GatePayload(
            gate="G3",
            kind="signoff",
            summary=f"Sign off {state['request_id']}: {report.status}, "
            f"{len(required)} warning(s) to acknowledge, "
            f"{sum(1 for c in report.checks if c.accepted_ref)} accepted finding(s)",
            options=list(G3_OPTIONS),
            required_acknowledgements=required,
            details={
                "delivery": "delivery.md",
                "accepted": state.get("accepted", []),
                "warnings": [c.model_dump(mode="json") for c in report.warns()],
            },
        )
        raw = interrupt({"type": "gate", **payload.model_dump(mode="json")})
        d = _decision(state, "G3", raw, rt.actor_human)
        decisions = _log_decision(rt, state, d)
        if d.decision == "approve":
            missing = sorted(set(required) - set(d.acknowledged_warnings))
            if missing:
                _audit(rt, state).append(
                    "escalation",
                    actor="workflow",
                    role="system",
                    summary=f"G3 approval refused: unacknowledged warnings {missing}",
                    payload={"missing": missing},
                )
                return {"decisions": decisions, "next": "gate_3"}
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "DELIVERED", f"signed off at G3 by {d.actor}"),
            }
        if d.decision == "request_changes":
            return {
                "decisions": decisions,
                "spec": None,
                "plan": None,
                "analysis": None,
                "report": None,
                "next": "intake",
                "feedback": [*state.get("feedback", []), d.comment or "changes requested"],
            }
        if d.decision == "reject":
            return {
                "decisions": decisions,
                "next": "end",
                **_status(rt, state, "REJECTED", "rejected at G3"),
            }
        return {"decisions": decisions, "next": "gate_3"}

    def finalize(state: RunState) -> dict[str, Any]:
        _write_manifest(rt, state, state.get("status", "IN_PROGRESS"))
        return {}

    g = StateGraph(RunState)
    nodes = {
        "intake": intake,
        "ask": ask,
        "gate_1": gate_1,
        "analyst": analyst,
        "validation": validation,
        "gate_2": gate_2,
        "gate_error": gate_error,
        "metric_proposal": metric_proposal,
        "review": review,
        "evidence": evidence,
        "gate_3": gate_3,
        "finalize": finalize,
    }
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    route_targets = {n: n for n in nodes if n != "finalize"} | {"end": "finalize"}
    for name in nodes:
        if name in ("finalize",):
            continue
        if name == "ask":
            g.add_edge("ask", "intake")
            continue
        g.add_conditional_edges(name, _route, route_targets)  # type: ignore[arg-type]
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def _route(state: RunState) -> str:
    return state.get("next", "end")


# ---- validation (shared with rerun) --------------------------------------------------------
def validate_analysis(
    rt: Runtime,
    spec: RequestSpec,
    analysis: AnalysisResult,
    snapshot_manifest: dict[str, Any],
    accepted: list[AcceptedFinding],
    results: dict[str, Any] | None = None,
) -> ValidationReport:
    """Validate the analysis on its snapshot. `results` (result_ref -> frame) overrides the
    stored result files (rerun validates the re-executed results)."""
    import pandas as pd

    from copilot.packs.compiler import compile_metric

    session = rt.open_session(
        rt.settings,
        rt.pack,
        request_id=spec.request_id,
        component="validation",
        profile="validation",
        snapshot=snapshot_from_manifest(snapshot_manifest),
    )
    try:
        run_dir = rt.run_dir(spec.request_id)
        targets = []
        for run in analysis.runs:
            req = metric_request(spec, run.metric_id)
            q = compile_metric(rt.pack, req, run.source_path, session.dialect, session.resolver)
            if q.sql_sha256 != run.sql_sha256:
                raise RuntimeError(f"{run.metric_id}: compiled SQL differs from the executed SQL")
            df = (results or {}).get(run.result_ref)
            if df is None:
                df = pd.read_parquet(run_dir / run.result_ref)
            targets.append(ValidationTarget(spec.request_id, req, q, df, run.non_template))
        report = run_checks(targets, rt.pack, session.backend, session.resolver, spec.request_id)
    finally:
        session.close()
    return apply_accepted(report, accepted)


def _write_manifest(rt: Runtime, state: RunState, status: str) -> None:
    rid = state["request_id"]
    run_dir = rt.run_dir(rid)
    spec = state.get("spec")
    analysis = state.get("analysis")
    report = state.get("report")
    p = rt.pack.pack
    manifest = RunManifest(
        request_id=rid,
        status=status,
        spec_sha256=sha256_text(canonical_json(spec)) if spec else None,
        spec=spec,
        pack_id=p.id,
        pack_version=p.version,
        pack_content_sha256=rt.pack.content_sha256,
        backend=rt.settings.backend,
        snapshot=state.get("snapshot"),
        source_path=state.get("source_path", "preferred"),
        query_runs=[QueryRun(**r) for r in (analysis or {}).get("runs", [])],
        validation_report_sha256=(report or {}).get("report_sha256"),
        accepted_findings=[AcceptedFinding(**a) for a in state.get("accepted", [])],
        gate_decisions=[GateDecision(**d) for d in state.get("decisions", [])],
        llm=state.get("budget", {}),
        audit_head_sha256=AuditLog(run_dir, rid).head,
    )
    write_manifest(run_dir, manifest)
