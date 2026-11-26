"""Terminal side of a run: build the runtime, render gates with Rich, answer interactively."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from copilot.config import Settings
from copilot.llm.gateway import Gateway
from copilot.llm.transport import make_transport
from copilot.packs.loader import load_pack
from copilot.workflow.graph import Runtime

console = Console()


def run_config_path(settings: Settings, request_id: str) -> Path:
    return settings.runs_dir / request_id / "run.json"


def save_run_config(settings: Settings, request_id: str, cfg: dict[str, Any]) -> None:
    path = run_config_path(settings, request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_run_config(settings: Settings, request_id: str) -> dict[str, Any]:
    path = run_config_path(settings, request_id)
    if not path.is_file():
        raise typer.BadParameter(f"unknown run {request_id} (no {path})")
    cfg: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return cfg


def build_runtime(
    settings: Settings, mode: str, recordings: Path | None, backend: str | None = None
) -> Runtime:
    if backend:
        settings = replace(settings, backend=backend)
    transport = make_transport(
        mode, recordings, timeout_s=settings.llm_timeout_s, max_retries=settings.llm_max_retries
    )
    return Runtime(settings, load_pack(), Gateway(settings, transport))


def show_pending(p: dict[str, Any]) -> None:
    if p.get("type") == "questions":
        t = Table(title="Intake questions", show_lines=False)
        t.add_column("topic")
        t.add_column("question")
        for q in p["questions"]:
            t.add_row(q["topic"], q["text"])
        console.print(t)
        return
    body = [f"[bold]{p.get('summary')}[/bold]", f"options: {', '.join(p.get('options', []))}"]
    if p.get("required_acknowledgements"):
        body.append("warnings to acknowledge: " + ", ".join(p["required_acknowledgements"]))
    details = p.get("details", {})
    if p.get("gate") == "G1" and "spec" in details:
        s = details["spec"]
        body.append(
            f"metrics {s['metrics']} · dims {s['dimensions']} · filters {s['filters']} · "
            f"{s['period']['start']}..{s['period']['end']} ({s['period']['grain']}) · "
            f"as-of {s['as_of_date']}"
        )
    if p.get("gate") == "G2":
        for key, e in sorted(details.get("explanations", {}).items()):
            body.append(f"{key}: {e.get('summary')} [dim](LLM-written)[/dim]")
    console.print(Panel("\n".join(body), title=f"{p.get('gate')} ({p.get('kind')})"))


def interactive(p: dict[str, Any]) -> Any:
    show_pending(p)
    if p.get("type") == "questions":
        return [
            {"topic": q["topic"], "answer": typer.prompt(f"[{q['topic']}] {q['text']}")}
            for q in p["questions"]
        ]
    options = p.get("options", [])
    decision = typer.prompt(f"decision ({'/'.join(options)}, or 'pause')")
    if decision == "pause":
        return None
    comment = typer.prompt("comment", default="", show_default=False) or None
    acks: list[str] = []
    if p.get("gate") == "G3" and decision == "approve":
        for w in p.get("required_acknowledgements", []):
            if typer.confirm(f"acknowledge warning {w}?"):
                acks.append(w)
    return {"decision": decision, "comment": comment, "acknowledged_warnings": acks}


def logging_answerer(inner: Any) -> Any:
    def answer(p: dict[str, Any]) -> Any:
        show_pending(p)
        value = inner(p)
        if value is not None and p.get("type") != "questions":
            console.print(f"  → [bold]{value.get('decision')}[/bold] ({value.get('actor')})")
        return value

    return answer


def summary(state: dict[str, Any]) -> None:
    rid = state.get("request_id")
    status = state.get("status")
    b = state.get("budget", {})
    console.print(
        f"[bold]{rid}[/bold]: {status}"
        + (f" ({state.get('status_reason')})" if state.get("status_reason") else "")
    )
    if state.get("paused_at"):
        console.print(
            f"paused at {state['paused_at'].get('gate') or 'questions'}; "
            f"continue with: copilot resume {rid}"
        )
    if b:
        console.print(
            f"LLM: {b.get('calls', 0)} calls, {b.get('tokens_in', 0):,} in / "
            f"{b.get('tokens_out', 0):,} out tokens, ${b.get('cost_usd', 0):.4f}"
        )


def show_rerun(report: Any) -> None:
    t = Table(title=f"rerun {report.request_id}")
    for col in ("item", "expected", "actual", "match"):
        t.add_column(col)
    for r in report.rows:
        t.add_row(r.item, r.expected[:16], r.actual[:16], "yes" if r.ok else "[red]NO[/red]")
    console.print(t)
