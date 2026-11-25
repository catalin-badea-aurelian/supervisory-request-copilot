"""M2 acceptance on BigQuery (run manually: `pytest -m bigquery`; never in CI).

Only the default dataset (all defects) is loaded in BigQuery. Defects sit only in Aug-Sep
2025, and August contains only D1, so the dataset is sliced by month:

- up to July 2025 is clean data: zero FAILs (this includes R1, June IT)
- August is D1 alone: exactly V-PK-01, V-REC-01 and V-DUAL-01, on ES
- September is D2 + D3 + D4: exactly their combined FAIL set

Statuses and affected slices must equal the DuckDB run on the same local Parquet files
(`poe data` writes them), and the report hash must be identical on rerun within BigQuery.
D2, D3 and D4 one at a time are covered on DuckDB only.

Cost: roughly 150 queries in all, mostly on partition-pruned `src_transactions`, each
capped by `maximum_bytes_billed`, labelled component=validation, request_id=m2-acceptance.
"""

from __future__ import annotations

import pytest

pytest.importorskip("google.cloud.bigquery")

from copilot.config import REPO_ROOT, Settings  # noqa: E402
from copilot.data.bigquery_backend import BigQueryBackend, source_resolver  # noqa: E402
from copilot.data.duckdb_backend import DuckDBBackend, load_parquet_into_duckdb  # noqa: E402
from copilot.validation.runner import run_checks  # noqa: E402
from copilot.validation.target import prepare_target  # noqa: E402
from tests.validation.acceptance import (  # noqa: E402
    comparable,
    expected,
    fails_by_month,
    findings,
    ids,
    run_suite,
    shapes,
)

pytestmark = pytest.mark.bigquery
PARQUET = REPO_ROOT / "data" / "parquet" / "default"


@pytest.fixture(scope="module")
def bq(pack):
    settings = Settings.from_env()
    if not settings.gcp_project_id:
        pytest.skip("GCP_PROJECT_ID not configured")
    backend = BigQueryBackend(settings, "validation", request_id="m2-acceptance")
    return backend, source_resolver(settings, pack)


@pytest.fixture(scope="module")
def bq_reports(bq, pack):
    backend, resolver = bq
    return run_suite(pack, backend, resolver)


@pytest.fixture(scope="module")
def duck_reports(tmp_path_factory, pack):
    if not (PARQUET / "src_transactions.parquet").exists():
        pytest.skip("run `poe data` first: the comparison reads the local Parquet files")
    db = tmp_path_factory.mktemp("bq_compare") / "default.duckdb"
    load_parquet_into_duckdb(PARQUET, db)
    backend = DuckDBBackend(db)
    try:
        return run_suite(pack, backend)
    finally:
        backend.close()


def test_month_sliced_expectations_hold_on_bigquery(bq_reports, expected_defects) -> None:
    fs = findings(bq_reports)
    d1, _ = expected(expected_defects, "D1")
    sept = set().union(*(expected(expected_defects, d)[0] for d in ("D2", "D3", "D4")))
    assert fails_by_month(fs) == {"2025-08": d1, "2025-09": sept}
    aug = [f for f in fs if f.status == "FAIL" and f.s["period"].startswith("2025-08")]
    assert {f.s.get("merchant_country", "ES") for f in aug} == {"ES"}
    assert ids([f for f in fs if f.shape in ("s1", "r1")], "FAIL") == set()
    assert ids([f for f in fs if f.shape == "r1"], "WARN") <= {"V-VAR-01"}


def test_bigquery_and_duckdb_agree_on_statuses_and_slices(bq_reports, duck_reports) -> None:
    a, b = comparable(bq_reports), comparable(duck_reports)
    assert a.keys() == b.keys()
    diffs = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
    assert not diffs, diffs


def test_report_hash_is_identical_on_rerun_in_bigquery(bq, pack) -> None:
    backend, resolver = bq
    req = shapes(pack)["s2"][0]

    def once() -> str:
        t = prepare_target(pack, req, backend, resolver, request_id="m2-acceptance")
        return run_checks([t], pack, backend, resolver).report_sha256

    assert once() == once()
