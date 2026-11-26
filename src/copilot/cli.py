"""Supervisory Request Copilot CLI.

M3: `copilot mcp serve`. M4: `ask`, `resume`, `rerun`, `audit verify`, `demo` and
`llm check-models`. LLM calls replay recordings by default; `--live` or `--record` are the
only ways to call the API (ADR 015).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer

from copilot import __version__
from copilot.config import REPO_ROOT

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


@contextmanager
def _llm_errors() -> Iterator[None]:
    """A missing recording or a refused live call ends the command with a clear message."""
    from copilot.llm.transport import TransportError

    try:
        yield
    except TransportError as e:
        typer.secho(f"LLM transport: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None


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
    with _llm_errors():
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
    with _llm_errors():
        state = drive(rt, request_id, answerer)
    cons.summary(state, mode)


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

    with _llm_errors():
        ok = run_demo(Settings.from_env(), scenario, "record" if record else "replay", backend)
    if not ok:
        raise typer.Exit(1)


@llm_app.command("check-models")
def check_models() -> None:
    """Live check that the configured model ids exist (one API call each). Explicit only."""
    from copilot.config import Settings
    from copilot.llm.anthropic_call import check_models as do_check

    for var, model, info in do_check(Settings.from_env()):
        typer.echo(f"{var}={model}: OK ({info.get('display_name')})")


dq_app = typer.Typer(help="Level 2 data-quality scan.")
app.add_typer(dq_app, name="dq")
ops_app = typer.Typer(help="Operational reporting to spb_ops.")
app.add_typer(ops_app, name="ops")


@dq_app.command("scan")
def dq_scan(
    as_of: str = typer.Option(..., "--as-of", help="Business as-of date (YYYY-MM-DD)."),
    backend: str | None = typer.Option(None, help="duckdb or bigquery (default from .env)."),
    metric: list[str] = typer.Option([], help="Limit to these metrics (default: all approved)."),
) -> None:
    """Run the check catalogue across metrics x months x countries; write ops_check_results."""
    from datetime import date

    from copilot.config import Settings
    from copilot.ops.scan import run_scan
    from copilot.ops.sink import make_sink
    from copilot.packs.loader import load_pack

    settings = Settings.from_env()
    out = run_scan(
        settings, load_pack(), date.fromisoformat(as_of), backend=backend, metrics=metric or None
    )
    where = make_sink(settings, backend).write("ops_check_results", out.rows, "append", out.run_id)
    c = out.counts()
    typer.echo(
        f"{out.run_id}: {sum(c.values())} check results (PASS {c['PASS']}, WARN {c['WARN']}, "
        f"FAIL {c['FAIL']}); {len(out.rows)} grid rows -> {where}"
    )


@ops_app.command("export")
def ops_export(
    backend: str | None = typer.Option(None, help="duckdb or bigquery (default from .env)."),
) -> None:
    """Rebuild ops_runs, ops_gate_decisions and ops_metric_catalog from the run folders."""
    from copilot.config import Settings
    from copilot.ops.export import export_rows
    from copilot.ops.sink import make_sink
    from copilot.packs.loader import load_pack

    settings = Settings.from_env()
    sink = make_sink(settings, backend)
    for table, rows in export_rows(settings.runs_dir, load_pack()).items():
        if table == "ops_check_results":  # shared with scans: replace only the request rows
            where = sink.replace_where(table, "run_type", "request", rows, "ops-export")
            table = f"{table} (run_type=request)"
        else:
            where = sink.write(table, rows, "replace", "ops-export")
        typer.echo(f"{table}: {len(rows)} rows -> {where}")


@app.command()
def views(
    deploy: bool = typer.Option(True, "--deploy/--no-deploy", help="Deploy to spb_marts."),
) -> None:
    """Generate the mart views from the domain pack (dashboards/views/) and deploy them."""
    from copilot.config import Settings
    from copilot.marts.deploy import deploy as do_deploy
    from copilot.marts.deploy import write_files
    from copilot.packs.loader import load_pack

    pack = load_pack()
    for path in write_files(pack).values():
        typer.echo(f"wrote {path.relative_to(path.parents[2]).as_posix()}")
    if deploy:
        settings = Settings.from_env()
        for name, est in do_deploy(pack, settings).items():
            size = "n/a" if est is None else f"{est / 1e6:,.1f} MB per full read"
            typer.echo(f"deployed {settings.dataset_prefix}_marts.{name} ({size})")


@app.command()
def lookml() -> None:
    """Generate the LookML project from the domain pack into looker/."""
    from copilot.lookml.write import LOOKER_DIR, write_project
    from copilot.packs.loader import load_pack

    for p in write_project(load_pack()):
        typer.echo(f"wrote {p.relative_to(LOOKER_DIR.parent).as_posix()}")


dashboard_app = typer.Typer(help="Agent-built oversight dashboard (spec -> HTML, ADR 016).")
app.add_typer(dashboard_app, name="dashboard")
gate_app = typer.Typer(help="Human gates outside a request run.")
app.add_typer(gate_app, name="gate")


@dashboard_app.command("build")
def dashboard_build(
    run_id: str | None = typer.Argument(None, help="Scan run id (default: the latest scan)."),
    scan: bool = typer.Option(False, "--scan", help="Use the latest scan run."),
    backend: str | None = typer.Option(None, help="duckdb or bigquery (default from .env)."),
    captions: str = typer.Option(
        "template",
        help="template (default), or replay / record / live: titles and captions by the gateway.",
    ),
) -> None:
    """Build the spec, render the candidate, reconcile every tile; the result waits for gate GD."""
    from copilot.config import Settings
    from copilot.dashboard.workflow import build_dashboard
    from copilot.packs.loader import load_pack

    if run_id and scan:
        raise typer.BadParameter("give a scan run id or --scan, not both")
    settings = Settings.from_env()
    pack = load_pack()
    cap = None
    if captions != "template":
        from copilot.llm.gateway import CallContext, RunBudget
        from copilot.workflow import console as cons

        rt = cons.build_runtime(settings, captions, REPO_ROOT / "demo" / "recordings" / "dashboard")
        cap = (rt.gateway, CallContext(RunBudget(settings.run_token_budget), actor="dashboard"))
    with _llm_errors():
        res = build_dashboard(settings, pack, scan_run_id=run_id, backend=backend, captions=cap)
    rep = res.reconciliation
    typer.echo(f"{res.dashboard_id}: {res.status}")
    typer.echo(f"spec: {res.spec_path.relative_to(REPO_ROOT).as_posix()}")
    typer.echo(f"candidate: {res.html_path}")
    typer.echo(f"reconciliation: {len(rep['tiles'])} tiles, {rep['mismatch_count']} mismatches")
    for m in rep["mismatches"][:20]:
        typer.echo(f"  MISMATCH {m['tile_id']} {m['key']}: shown {m['shown']} vs {m['reference']}")
    if res.status == "BLOCKED":
        raise typer.Exit(1)
    typer.echo(f"next: copilot gate GD {res.dashboard_id} --approve --reason '...'")


@gate_app.command("GD")
def gate_gd(
    dashboard_id: str,
    approve: bool = typer.Option(False, "--approve"),
    reject: bool = typer.Option(False, "--reject"),
    reason: str = typer.Option(..., "--reason", help="Why (recorded in the audit log)."),
    actor: str = typer.Option("approver", help="Who decides."),
) -> None:
    """Gate GD: approve or reject a built dashboard. Only approval publishes to docs/dashboard/."""
    from copilot.config import Settings
    from copilot.dashboard.workflow import GateError, decide_gd

    if approve == reject:
        raise typer.BadParameter("give exactly one of --approve or --reject")
    try:
        meta = decide_gd(
            Settings.from_env(),
            dashboard_id,
            "approve" if approve else "reject",
            reason,
            actor=actor,
        )
    except GateError as e:
        typer.secho(f"GD refused: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{dashboard_id}: {meta['status']}")
    if approve:
        typer.echo("published: docs/dashboard/index.html (+ evidence.md, manifest.json)")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", help="Show the version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


if __name__ == "__main__":
    app()
