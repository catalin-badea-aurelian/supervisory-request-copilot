"""Golden tests against BigQuery (run manually: `pytest -m bigquery`; never in CI).

The BigQuery dataset is the default one (defects planted), so only requests up to July 2025
are compared, against the pandas reference built from the same local Parquet files.
"""

import pytest

pytest.importorskip("google.cloud.bigquery")

from copilot.config import REPO_ROOT, Settings  # noqa: E402
from copilot.data.bigquery_backend import BigQueryBackend, source_resolver  # noqa: E402
from copilot.packs.compiler import MetricRequest, compile_metric  # noqa: E402
from tests.reference.metrics import load_frames, reference_metric  # noqa: E402
from tests.test_golden_duckdb import (  # noqa: E402
    AS_OF,
    METRICS,
    SHAPES,
    _assert_equal,
    _normalise,
)

pytestmark = pytest.mark.bigquery
PARQUET = REPO_ROOT / "data" / "parquet" / "default"


@pytest.fixture(scope="module")
def backend():
    settings = Settings.from_env()
    if not settings.gcp_project_id:
        pytest.skip("GCP_PROJECT_ID not configured")
    return settings, BigQueryBackend(settings, "validation")


@pytest.fixture(scope="module")
def frames():
    if not (PARQUET / "src_transactions.parquet").exists():
        pytest.skip("run `poe data` first: the reference reads the local Parquet files")
    return load_frames(PARQUET)


@pytest.mark.parametrize("path", ["preferred", "independent"])
@pytest.mark.parametrize("shape_name", ["q1q2_country_mcc", "monthly_filtered", "mcc_slice"])
@pytest.mark.parametrize("metric_id", METRICS)
def test_bigquery_template_equals_reference(backend, frames, pack, metric_id, path, shape_name):
    settings, bq = backend
    shape = SHAPES[shape_name]
    req = MetricRequest(
        metric_id=metric_id,
        as_of=AS_OF,
        period_start=shape["period_start"],
        period_end=shape["period_end"],
        grain=shape["grain"],
        dims=tuple(shape["dims"]),
        filters=shape.get("filters", {}),
    )
    q = compile_metric(pack, req, path, "bigquery", source_resolver(settings, pack))
    actual = _normalise(bq.query(q.sql, q.params), req.dims)
    expected = _normalise(
        reference_metric(
            frames,
            metric_id,
            req.period_start,
            req.period_end,
            AS_OF,
            req.grain,
            req.dims,
            req.filters,
        ),
        req.dims,
    )
    _assert_equal(actual, expected, req.dims)
