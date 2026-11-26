"""Supervisory Request Copilot CLI.

M3: `copilot mcp serve`. M4: `ask`, `resume`, `rerun`, `audit verify`, `demo` and
`llm check-models`. LLM calls replay recordings by default; `--live` or `--record` are the
only ways to call the API (ADR 015).
"""

from __future__ import annotations

from pathlib import Path

import typer

from copilot import __version__

app = typer.Typer(name="copilot", help="Supervisory Request Copilot")
mcp_app = typer.Typer(help="Governed-data MCP server (Level 1 self-service).")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Start the governed-data MCP server on stdio (for Claude Desktop and other clients)."""
    from copilot.mcp_server.server import serve

    serve()


audit_app = typer.Typer(help="Audit log commands.")
app.add_typer(audit_app, name="audit")
llm_app = typer.Typer(help="LLM gateway commands.")
app.add_typer(llm_app, name="llm")


def _mode(live: bool, record: bool) -> str | None:
    if live and record:
        raise typer.BadParameter("use either --live or --record")
    return "live" if live else "record" if record else None


@app.command()
def ask(
    request: str = typer.Argument("", help="The request text (taken from --answers if omitted)."),
    answers: Path | None = typer.Option(None, help="Scripted answers (demo/answers/*.yaml)."),
    live: bool = typer.Option(False, "--live", help="Call the LLM API (costs money)."),
    record: bool = typer.Option(False, "--record", help="Call the API and store recordings."),
    recordings: Path | None = typer.Option(None, help="Recordings directory for replay/record."),
    backend: str | None = typer.Option(None, help="duckdb or bigquery (default from .env)."),
    requester: str = typer.Option("requester", help="Who is asking."),
) -> None:
    """Start a run; human gates are answered in the terminal or from --answers."""
    from copilot.config import Settings
    from copilot.workflow import console as cons
    from copilot.workflow.runner import drive, new_request_id
    from copilot.workflow.scripted import Script

    settings = Settings.from_env()
    script = Script.load(answers) if answers else None
    text = request or (script.request if script else "")
    if not text:
        raise typer.BadParameter("give a request text or --answers")
    rid = new_request_id()
    mode = _mode(live, record) or settings.llm_mode
    rec = recordings or (script.recordings if script else settings.runs_dir / rid / "recordings")
    cons.save_run_config(
        settings,
        rid,
        {
            "mode": mode,
            "recordings": str(rec),
            "backend": backend or settings.backend,
            "answers": str(answers) if answers else None,
        },
    )
    rt = cons.build_runtime(settings, mode, rec, backend)
    answerer = cons.logging_answerer(script) if script else cons.interactive
    state = drive(
        rt, rid, answerer, raw_request=text, requester=script.requester if script else requester
    )
    cons.summary(state, mode)


@app.command()
def resume(
    request_id: str,
    answers: Path | None = typer.Option(None, help="Scripted answers for the remaining gates."),
    live: bool = typer.Option(False, "--live", help="Call the LLM API (costs money)."),
    record: bool = typer.Option(False, "--record", help="Call the API and store recordings."),
) -> None:
    """Resume a paused run (for example waiting at G3)."""
    from copilot.config import Settings
    from copilot.workflow import console as cons
    from copilot.workflow.runner import drive
    from copilot.workflow.scripted import Script

    settings = Settings.from_env()
    cfg = cons.load_run_config(settings, request_id)
    mode = _mode(live, record) or "replay"
    rt = cons.build_runtime(settings, mode, Path(cfg["recordings"]), cfg.get("backend"))
    answerer = cons.logging_answerer(Script.load(answers)) if answers else cons.interactive
    cons.summary(drive(rt, request_id, answerer), mode)


@app.command()
def rerun(request_id: str) -> None:
    """Reproduce a past run from its manifest and compare result hashes (no LLM calls)."""
    from copilot.config import Settings
    from copilot.workflow import console as cons
    from copilot.workflow.rerun import rerun as do_rerun

    settings = Settings.from_env()
    cfg = cons.load_run_config(settings, request_id)
    rt = cons.build_runtime(settings, "replay", Path(cfg["recordings"]), cfg.get("backend"))
    report = do_rerun(rt, request_id)
    cons.show_rerun(report)
    if not report.ok:
        raise typer.Exit(1)


@audit_app.command("verify")
def audit_verify(request_id: str) -> None:
    """Recompute the hash chain of a run's audit log."""
    from copilot.audit.log import verify_chain
    from copilot.config import Settings

    rep = verify_chain(Settings.from_env().runs_dir / request_id)
    if rep.ok:
        typer.echo(f"{request_id}: audit chain OK ({rep.events} events, head {rep.head[:16]})")
        return
    typer.echo(f"{request_id}: audit chain BROKEN at event {rep.first_bad_seq}: {rep.reason}")
    raise typer.Exit(1)


@app.command()
def demo(
    scenario: list[str] = typer.Option(["s1", "s2"], help="Scenarios to run."),
    record: bool = typer.Option(False, "--record", help="Call the API and re-record."),
    backend: str | None = typer.Option(None, help="duckdb or bigquery (default from .env)."),
) -> None:
    """Run the demo scenarios with scripted answers (replay by default: no API calls)."""
    from copilot.config import Settings
    from copilot.workflow.demo import run_demo

    if not run_demo(Settings.from_env(), scenario, "record" if record else "replay", backend):
        raise typer.Exit(1)


@llm_app.command("check-models")
def check_models() -> None:
    """Live check that the configured model ids exist (one API call each). Explicit only."""
    from copilot.config import Settings
    from copilot.llm.anthropic_call import check_models as do_check

    for var, model, info in do_check(Settings.from_env()):
        typer.echo(f"{var}={model}: OK ({info.get('display_name')})")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", help="Show the version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


if __name__ == "__main__":
    app()
