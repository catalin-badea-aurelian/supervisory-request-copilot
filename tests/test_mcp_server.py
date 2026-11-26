"""Governed MCP server: exactly five tools, no SQL tool, no thresholds, guarded execution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date

import pytest
from mcp import Client

from copilot.config import Settings
from copilot.data.session import open_session
from copilot.mcp_server.server import TOOL_NAMES, build_server, default_session_factory
from copilot.mcp_server.tools import GovernedTools, ToolInputError
from copilot.packs.compiler import MetricRequest, compile_metric

THRESHOLD_KEYS = {
    "tolerances",
    "checks",
    "ewi",
    "reconciles_to",
    "precomputed_columns",
    "sql_template",
    "independent_sql_template",
}


@pytest.fixture
def tools(pack, small_default, tmp_path) -> GovernedTools:
    st = replace(Settings.from_env(), backend="duckdb", parquet_dir=small_default.parquet_dir)
    return GovernedTools(pack, default_session_factory(st, pack), tmp_path / "mcp")


def _call(server, name: str, args: dict):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args)

    return asyncio.run(go())


def test_exactly_the_five_governed_tools_and_none_takes_sql(tools) -> None:
    async def go():
        async with Client(build_server(tools)) as c:
            return (await c.list_tools()).tools

    listed = asyncio.run(go())
    assert sorted(t.name for t in listed) == sorted(TOOL_NAMES)
    params = {t.name: set(t.input_schema.get("properties", {})) for t in listed}
    assert params["run_metric"] == {
        "metric_id",
        "period_start",
        "period_end",
        "as_of",
        "grain",
        "dims",
        "filters",
    }
    assert params["search_catalog"] == {"query"}  # keywords, matched in Python; never SQL
    assert not any("sql" in p.lower() for ps in params.values() for p in ps)


def test_definitions_expose_no_thresholds_or_check_sets(tools, pack) -> None:
    for m in tools.list_metrics():
        d = tools.get_metric_definition(m["metric_id"])
        assert not THRESHOLD_KEYS & set(d), m["metric_id"]
        assert d["version"] == pack.metrics[m["metric_id"]].version
    with pytest.raises(ToolInputError):
        tools.get_metric_definition("no_such_metric")


def test_search_catalog_finds_metrics_and_tables(tools) -> None:
    hits = tools.search_catalog("chargeback")
    assert any(h["kind"] == "metric" and h["id"] == "chargeback_rate_value" for h in hits)
    assert all(h["kind"] in ("metric", "dimension", "table") for h in hits)
    with pytest.raises(ToolInputError):
        tools.search_catalog("   ")


def test_describe_table_is_limited_to_metric_sources(tools) -> None:
    out = tools.describe_table("src_merchants")
    assert {"merchant_id", "merchant_country"} <= {c["name"] for c in out["columns"]}
    for table in (
        "ledger_control_totals",
        "ewi_thresholds",
        "prior_submissions",
        "meta_load_log",
        "nope",
    ):
        with pytest.raises(ToolInputError):
            tools.describe_table(table)


def test_run_metric_equals_the_governed_template(tools, pack, small_default) -> None:
    out = tools.run_metric(
        "chargeback_rate_value",
        "2025-01-01",
        "2025-06-30",
        "2025-10-15",
        "quarter",
        ["period", "merchant_country"],
        {"merchant_country": ["IT", "DE"]},
    )
    st = replace(Settings.from_env(), backend="duckdb", parquet_dir=small_default.parquet_dir)
    s = open_session(st, pack, request_id="ref", component="test")
    try:
        req = MetricRequest(
            "chargeback_rate_value",
            date(2025, 1, 1),
            date(2025, 6, 30),
            date(2025, 10, 15),
            "quarter",
            ("period", "merchant_country"),
            {"merchant_country": ("IT", "DE")},
        )
        q = compile_metric(pack, req, "preferred", "duckdb", s.resolver)
        _, rec = s.backend.execute(q.sql, q.params)
    finally:
        s.close()
    assert out["result_sha256"] == rec.result_sha256
    assert out["sql_sha256"] == rec.sql_sha256
    assert out["template_id"] == "chargeback_rate_value@1.1.0:preferred"
    assert out["snapshot_id"] == s.snapshot_id
    assert out["rows"] == 4 and not out["truncated"]


@pytest.mark.parametrize(
    "args",
    [
        {"metric_id": "made_up_metric"},
        {"period_start": "2025-01-15"},  # not aligned to the grain
        {"dims": ["merchant_id"]},  # not an allowed dimension
        {"filters": {"merchant_country": ["DE'; DROP TABLE x; --"]}},
        {"grain": "week"},
        {"as_of": "yesterday"},
    ],
)
def test_run_metric_refuses_invalid_requests(tools, args) -> None:
    base = {
        "metric_id": "chargeback_rate_value",
        "period_start": "2025-01-01",
        "period_end": "2025-06-30",
        "as_of": "2025-10-15",
        "grain": "month",
    }
    with pytest.raises(ToolInputError):
        tools.run_metric(**{**base, **args})


def test_refusals_reach_the_client_as_tool_errors_with_a_reason(tools) -> None:
    res = _call(
        build_server(tools),
        "run_metric",
        {
            "metric_id": "chargeback_rate_value",
            "period_start": "2025-01-15",
            "period_end": "2025-06-30",
            "as_of": "2025-10-15",
        },
    )
    assert res.is_error
    assert "ToolInputError" in res.content[0].text


def test_run_metric_through_the_client(tools) -> None:
    res = _call(
        build_server(tools),
        "run_metric",
        {
            "metric_id": "settled_volume_eur",
            "period_start": "2025-07-01",
            "period_end": "2025-07-31",
            "as_of": "2025-10-15",
            "dims": ["period"],
        },
    )
    assert not res.is_error
    payload = res.structured_content
    assert payload["rows"] == 1 and payload["metric_id"] == "settled_volume_eur"


def test_every_call_is_logged(tools) -> None:
    tools.list_metrics()
    tools.run_metric("settled_volume_eur", "2025-07-01", "2025-07-31", "2025-10-15")
    lines = [json.loads(x) for x in tools._log.read_text(encoding="utf-8").splitlines()]
    assert [e["tool"] for e in lines] == ["list_metrics", "run_metric"]
    assert lines[1]["result_sha256"] and lines[1]["pack"].startswith("payments@")


def test_search_catalog_does_not_list_checker_only_tables(tools) -> None:
    assert not [h for h in tools.search_catalog("load") if h["kind"] == "table"]
    assert not [h for h in tools.search_catalog("ledger") if h["kind"] == "table"]


def test_run_metric_states_the_as_of_it_applied_and_what_the_snapshot_means(tools) -> None:
    out = tools.run_metric("settled_volume_eur", "2025-07-01", "2025-07-31", "2025-10-15")
    assert out["as_of_applied"] == "2025-10-15"
    assert "_loaded_at" in out["as_of_rule"]
    assert out["snapshot_id"].startswith("duckdb:")
    assert "not the business as-of date" in out["snapshot_meaning"]
    d = tools.get_metric_definition("chargeback_rate_value")
    assert "_loaded_at" in d["as_of"]


def test_live_snapshot_meaning_is_explicit() -> None:
    from copilot.mcp_server.tools import snapshot_meaning

    text = snapshot_meaning("live@2026-11-26T10:13:40+00:00")
    assert "2026-11-26T10:13:40+00:00" in text
    assert "not the business as-of date" in text
