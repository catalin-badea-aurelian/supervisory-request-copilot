"""Governed-data MCP server (`copilot mcp serve`, stdio) for Level 1 self-service (S0).

Built on the official Python MCP SDK's `MCPServer` (the 2.x name of FastMCP). Exactly five
tools; there is no free-SQL tool (FR-P2). Each tool delegates to `GovernedTools`, which runs
through the guarded data-access layer with component=mcp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from copilot.config import REPO_ROOT, Settings
from copilot.data.guarded import BytesLimitExceeded, QueryTimeout
from copilot.data.policy import PolicyViolation
from copilot.data.session import DataSession, open_session
from copilot.mcp_server.tools import GovernedTools, ToolInputError
from copilot.packs.loader import load_pack
from copilot.packs.models import DomainPack

TOOL_NAMES = (
    "list_metrics",
    "get_metric_definition",
    "search_catalog",
    "describe_table",
    "run_metric",
)

INSTRUCTIONS = (
    "Governed metrics of Synthetic Payments Bank (SPB), a fictional EU payment institution "
    "with synthetic data. Use list_metrics and get_metric_definition to find a certified "
    "metric, then run_metric. Only governed templates run; there is no SQL tool."
)


def default_session_factory(settings: Settings, pack: DomainPack):
    def factory(request_id: str) -> DataSession:
        return open_session(
            settings,
            pack,
            request_id=request_id,
            component="mcp",
            profile="metric",
            snapshot_mode="live",  # BigQuery: no per-question snapshot; DuckDB: Parquet snapshot
        )

    return factory


_USER_ERRORS = (ToolInputError, PolicyViolation, BytesLimitExceeded, QueryTimeout)


def _guard(fn: Any, *args: Any) -> Any:
    """Report expected refusals to the client as tool errors with their reason."""
    try:
        return fn(*args)
    except _USER_ERRORS as e:
        raise ToolError(f"{type(e).__name__}: {e}") from None


def build_server(tools: GovernedTools) -> MCPServer:
    server: MCPServer = MCPServer("spb-governed-metrics", instructions=INSTRUCTIONS)

    @server.tool()
    def list_metrics() -> list[dict[str, Any]]:
        """List the approved glossary metrics (id, name, version, unit, allowed dimensions)."""
        return _guard(tools.list_metrics)

    @server.tool()
    def get_metric_definition(metric_id: str) -> dict[str, Any]:
        """Definition, components, attribution, sources, owner and version of one metric."""
        return _guard(tools.get_metric_definition, metric_id)

    @server.tool()
    def search_catalog(query: str) -> list[dict[str, str]]:
        """Keyword search over metrics, dimensions and tables."""
        return _guard(tools.search_catalog, query)

    @server.tool()
    def describe_table(table: str) -> dict[str, Any]:
        """Columns and types of a source table available to governed metrics."""
        return _guard(tools.describe_table, table)

    @server.tool()
    def run_metric(
        metric_id: str,
        period_start: str,
        period_end: str,
        as_of: str,
        grain: str = "month",
        dims: list[str] | None = None,
        filters: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Run a governed metric. Dates are ISO (YYYY-MM-DD); the period must align to the
        grain (month or quarter). dims: any of the metric's allowed_dimensions, e.g.
        ["period", "merchant_country"]. filters: e.g. {"merchant_country": ["DE", "FR"]}.

        as_of is the business as-of date and is always applied in the SQL: rows loaded after
        it are excluded (the result echoes it as as_of_applied). snapshot_id describes which
        physical table state was read (live@<time> = current tables at that system time); it
        is not the as-of date."""
        return _guard(
            tools.run_metric, metric_id, period_start, period_end, as_of, grain, dims, filters
        )

    return server


def make_server(settings: Settings | None = None, log_dir: Path | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    pack = load_pack("payments")
    tools = GovernedTools(
        pack, default_session_factory(settings, pack), log_dir or REPO_ROOT / "runs" / "mcp"
    )
    return build_server(tools)


def serve() -> None:
    make_server().run("stdio")
