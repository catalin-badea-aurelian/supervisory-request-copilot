"""The compiler enforces governance rules before any SQL exists."""

from datetime import date

import pytest
import sqlglot

from copilot.packs.compiler import CompileError, MetricRequest, compile_metric

Q = dict(period_start=date(2025, 1, 1), period_end=date(2025, 3, 31), as_of=date(2025, 10, 15))


def req(metric="chargeback_rate_value", **kw):
    base = dict(metric_id=metric, grain="quarter", dims=("period", "merchant_country"), filters={})
    base.update(Q)
    base.update(kw)
    return MetricRequest(**base)


def test_disallowed_dimension_is_rejected(pack) -> None:
    with pytest.raises(CompileError, match="not allowed"):
        compile_metric(pack, req(dims=("period", "payment_method")))


def test_filter_values_are_validated_against_the_pack(pack) -> None:
    with pytest.raises(CompileError, match="unknown value"):
        compile_metric(pack, req(filters={"merchant_country": ("DE'; DROP TABLE x;--",)}))


def test_filter_on_period_or_unknown_key_is_rejected(pack) -> None:
    with pytest.raises(CompileError):
        compile_metric(pack, req(filters={"period": ("2025-01-01",)}))
    with pytest.raises(CompileError):
        compile_metric(pack, req(filters={"risk_tier": ("high",)}))


@pytest.mark.parametrize(
    "start,end,grain",
    [
        (date(2025, 1, 15), date(2025, 3, 31), "quarter"),
        (date(2025, 2, 1), date(2025, 4, 30), "quarter"),
        (date(2025, 1, 1), date(2025, 3, 30), "quarter"),
        (date(2025, 3, 1), date(2025, 1, 31), "month"),
    ],
)
def test_misaligned_period_is_rejected(pack, start, end, grain) -> None:
    with pytest.raises(CompileError):
        compile_metric(pack, req(period_start=start, period_end=end, grain=grain))


def test_unknown_metric_is_rejected(pack) -> None:
    with pytest.raises(CompileError, match="unknown metric"):
        compile_metric(pack, req(metric="new_merchant_volume_share"))


@pytest.mark.parametrize("dialect", ["duckdb", "bigquery"])
def test_independent_path_never_reads_the_group_product(pack, dialect) -> None:
    for mid in pack.metrics:
        dims = ("period", "merchant_country")
        q = compile_metric(
            pack, req(metric=mid, dims=dims), "independent", dialect, lambda n: f"`p.spb_raw.{n}`"
        )
        assert "grp_merchant_monthly" not in q.sql, mid


def test_compiled_sql_is_one_read_only_select(pack) -> None:
    for mid in pack.metrics:
        for path in ("preferred", "independent"):
            q = compile_metric(pack, req(metric=mid), path, "duckdb")
            tree = sqlglot.parse(q.sql, read="duckdb")
            assert len(tree) == 1
            assert isinstance(tree[0], sqlglot.exp.Select), mid


def test_bigquery_uses_named_params_and_resolver(pack) -> None:
    q = compile_metric(pack, req(), "independent", "bigquery", lambda n: f"`proj.spb_raw.{n}`")
    assert "@period_start" in q.sql and "@as_of" in q.sql
    assert "`proj.spb_raw.src_transactions`" in q.sql
    assert set(q.params) == {"period_start", "period_end", "as_of"}
    with pytest.raises(CompileError, match="resolver"):
        compile_metric(pack, req(), "independent", "bigquery")


def test_compilation_is_deterministic(pack) -> None:
    a = compile_metric(pack, req(), "preferred", "duckdb")
    b = compile_metric(pack, req(), "preferred", "duckdb")
    assert a.sql_sha256 == b.sql_sha256 and a.template_id == "chargeback_rate_value@1.1.0:preferred"


def test_filters_render_as_sorted_literals(pack) -> None:
    q = compile_metric(pack, req(filters={"merchant_country": ("FR", "DE")}), "preferred", "duckdb")
    assert "merchant_country IN ('DE', 'FR')" in q.sql
