"""Supervisory Request Copilot CLI.

Full command set (ask, resume, rerun, audit verify, dq scan, ops export,
mcp serve) lands milestone by milestone per CLAUDE.md's build order; this is
the M0 entry point so `uv run copilot --version` resolves to something real.
"""

from __future__ import annotations

import typer

from copilot import __version__

app = typer.Typer(name="copilot", help="Supervisory Request Copilot")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", help="Show the version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


if __name__ == "__main__":
    app()
