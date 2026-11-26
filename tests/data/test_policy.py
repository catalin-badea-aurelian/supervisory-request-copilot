"""SqlPolicy: single SELECT only, no DDL/DML/scripting, table allowlist, forced LIMIT."""

from __future__ import annotations

import pytest

from copilot.data.policy import PolicyViolation, SqlPolicy, enforce_limit

BQ_ALLOWED = ["`p.spb_raw.src_transactions`", "`p.spb_group.grp_merchant_monthly`"]
DUCK_ALLOWED = ["src_transactions", "src_merchants"]


@pytest.fixture
def bq() -> SqlPolicy:
    return SqlPolicy("bigquery", BQ_ALLOWED)


@pytest.fixture
def duck() -> SqlPolicy:
    return SqlPolicy("duckdb", DUCK_ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM `p.spb_raw.src_transactions`",
        "SELECT * FROM p.spb_raw.src_transactions WHERE amount_eur > @x",
        "WITH t AS (SELECT * FROM `p.spb_raw.src_transactions`) SELECT COUNT(*) FROM t",
        "SELECT 1 AS a UNION ALL SELECT 2",
        "SELECT a.x FROM `p.spb_raw.src_transactions` a "
        "JOIN `p.spb_group.grp_merchant_monthly` b ON a.x = b.x",
        "SELECT * FROM (SELECT * FROM `p`.`spb_raw`.`src_transactions`)",
        "SELECT * FROM UNNEST([1, 2]) AS v",
    ],
)
def test_allowed_bigquery_selects(bq: SqlPolicy, sql: str) -> None:
    bq.check(sql)


def test_reports_the_tables_read(bq: SqlPolicy) -> None:
    res = bq.check(
        "WITH t AS (SELECT * FROM `p.spb_raw.src_transactions`) SELECT * FROM t "
        "JOIN `p.spb_group.grp_merchant_monthly` USING (x)"
    )
    assert res.tables == ("p.spb_group.grp_merchant_monthly", "p.spb_raw.src_transactions")


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE `p.spb_raw.src_transactions`", "not_a_select"),
        ("INSERT INTO `p.spb_raw.src_transactions` (x) VALUES (1)", "not_a_select"),
        ("UPDATE `p.spb_raw.src_transactions` SET x = 1 WHERE TRUE", "not_a_select"),
        ("DELETE FROM `p.spb_raw.src_transactions` WHERE TRUE", "not_a_select"),
        (
            "MERGE `p.spb_raw.src_transactions` t USING `p.spb_raw.src_transactions` s ON FALSE "
            "WHEN NOT MATCHED THEN INSERT ROW",
            "not_a_select",
        ),
        ("CREATE TABLE `p.spb_raw.x` AS SELECT 1 AS a", "not_a_select"),
        ("TRUNCATE TABLE `p.spb_raw.src_transactions`", "not_a_select"),
        ("EXPORT DATA OPTIONS (uri='gs://b/*.csv', format='CSV') AS SELECT 1", "not_a_select"),
        ("DECLARE x INT64", "not_a_select"),
        ("BEGIN SELECT 1; END", "not_single_statement"),
        ("CALL p.ds.proc()", "not_a_select"),
        ("SELECT 1; SELECT 2", "not_single_statement"),
        ("SELECT 1; DROP TABLE `p.spb_raw.src_transactions`", "not_single_statement"),
        ("CREATE TEMP FUNCTION f() AS (1); SELECT f()", "not_single_statement"),
        ("SELECT * FROM `p.spb_control.ewi_thresholds`", "table_not_allowed"),
        ("SELECT * FROM spb_raw.src_transactions", "table_not_allowed"),
        ("SELECT * FROM `p.spb_raw.INFORMATION_SCHEMA.TABLES`", "information_schema"),
        ("SELECT * FROM `p.region-eu`.INFORMATION_SCHEMA.JOBS", "information_schema"),
        ("SELECT * FROM EXTERNAL_QUERY('conn', 'select 1')", "forbidden_function"),
        ("SELECT * FROM (SELECT * FROM `p.spb_control.prior_submissions`)", "table_not_allowed"),
        ("WITH t AS (SELECT * FROM `p.spb_ops.runs`) SELECT * FROM t", "table_not_allowed"),
        ("", "not_single_statement"),
        ("SELEC 1 FROM", "unparseable"),
    ],
)
def test_rejected_bigquery_statements(bq: SqlPolicy, sql: str, code: str) -> None:
    with pytest.raises(PolicyViolation) as e:
        bq.check(sql)
    assert e.value.code == code, e.value


def test_a_cte_cannot_shadow_a_qualified_table(bq: SqlPolicy) -> None:
    with pytest.raises(PolicyViolation, match="table_not_allowed"):
        bq.check("WITH x AS (SELECT 1) SELECT * FROM `p.spb_control.x`")


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("COPY src_transactions TO 'out.csv'", "not_a_select"),
        ("ATTACH 'other.duckdb'", "not_a_select"),
        ("PRAGMA version", "not_a_select"),
        ("INSTALL httpfs", "not_a_select"),
        ("LOAD httpfs", "not_a_select"),
        ("SET threads = 1", "not_a_select"),
        ("SELECT * FROM read_parquet('x.parquet')", "forbidden_function"),
        ("SELECT * FROM read_csv_auto('x.csv')", "forbidden_function"),
        ("SELECT * FROM 'data/x.parquet'", "table_not_allowed"),
        ("SELECT * FROM range(10)", "table_function"),
        ("SELECT getenv('HOME')", "forbidden_function"),
        ("SELECT * FROM ewi_thresholds", "table_not_allowed"),
    ],
)
def test_rejected_duckdb_statements(duck: SqlPolicy, sql: str, code: str) -> None:
    with pytest.raises(PolicyViolation) as e:
        duck.check(sql)
    assert e.value.code == code, e.value


def test_allowed_duckdb_select(duck: SqlPolicy) -> None:
    duck.check(
        "SELECT m.merchant_country, COUNT(*) FROM src_transactions t "
        "JOIN src_merchants m USING (merchant_id) GROUP BY 1"
    )


@pytest.mark.parametrize(
    ("sql", "expected_limit"),
    [
        ("SELECT * FROM t", 20),
        ("SELECT * FROM t LIMIT 5", 5),
        ("SELECT * FROM t LIMIT 500", 20),
        ("WITH a AS (SELECT * FROM t) SELECT * FROM a ORDER BY 1 LIMIT 100", 20),
    ],
)
def test_enforce_limit(sql: str, expected_limit: int) -> None:
    out = enforce_limit(sql, 20, "bigquery")
    assert out.upper().endswith(f"LIMIT {expected_limit}")
    assert out.upper().count("LIMIT") == 1
