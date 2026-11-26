"""Offline checks of the BigQuery environment spec, labels and the clean-data guard."""

import pytest

from copilot.data.bigquery_clients import job_labels
from data.generator.__main__ import main
from data.generator.write import SCHEMAS
from infra.bigquery import spec

TYPE_MAP = {
    "string": "STRING",
    "date32[day]": "DATE",
    "timestamp[us, tz=UTC]": "TIMESTAMP",
    "decimal128(18, 2)": "NUMERIC",
    "int64": "INT64",
    "double": "FLOAT64",
}


def test_table_specs_match_the_generated_parquet_schemas() -> None:
    assert {t.name for t in spec.TABLES} == set(SCHEMAS)
    for t in spec.TABLES:
        generated = [(c, TYPE_MAP[str(ty)]) for c, ty, _ in SCHEMAS[t.name]]
        assert list(t.columns) == generated, t.name


def test_access_matrix_is_least_privilege() -> None:
    for layer in ("raw", "group", "control"):
        assert spec.ACCESS[layer] == {"loader": "WRITER", "copilot": "READER"}
    for layer in ("raw", "group", "control", "snapshots"):
        assert "looker" not in spec.ACCESS[layer]
    for layer in ("snapshots", "ops", "marts"):
        assert "loader" not in spec.ACCESS[layer]
    assert spec.ACCESS["ops"]["looker"] == "READER"
    assert spec.ACCESS["marts"]["looker"] == "READER"
    assert spec.AUTHORIZED_DATASETS == {"marts": ("raw", "group", "control")}


def test_snapshot_role_is_narrow_and_dataset_scoped() -> None:
    # ADR 014: no basic role on spb_snapshots; a custom role bound on that dataset only
    assert spec.ACCESS["snapshots"] == {}
    assert spec.DATASET_IAM_BINDINGS == {"snapshots": {"copilot": spec.SNAPSHOT_ROLE_ID}}
    perms = set(spec.SNAPSHOT_ROLE_PERMISSIONS)
    assert "bigquery.tables.create" in perms  # needed to create a snapshot (residual)
    assert not perms & {
        "bigquery.tables.updateData",
        "bigquery.tables.delete",
        "bigquery.tables.update",
        "bigquery.datasets.update",
        "bigquery.tables.setIamPolicy",
        "bigquery.datasets.setIamPolicy",
    }


def test_transactions_table_physical_design() -> None:
    t = next(t for t in spec.TABLES if t.name == "src_transactions")
    assert t.partition_field == "txn_ts" and t.require_partition_filter
    assert t.cluster == ("merchant_id", "payment_method")
    assert "merchant_country" not in dict(t.columns)  # deliberately not denormalised


def test_job_labels_are_valid_bigquery_labels() -> None:
    assert job_labels("Analyst", "REQ-2025-0042") == {
        "component": "analyst",
        "request_id": "req-2025-0042",
    }
    assert job_labels("scan")["request_id"] == "none"


def test_clean_data_into_bigquery_is_refused_without_force(capsys, tmp_path) -> None:
    assert main(["--target", "bigquery", "--no-defects", "--out", str(tmp_path)]) == 2
    assert "--force" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())  # refused before anything was generated or loaded


def test_scaled_down_dataset_is_never_loaded_into_bigquery(tmp_path) -> None:
    assert main(["--target", "bigquery", "--scale", "0.1", "--out", str(tmp_path)]) == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("layer", spec.LAYERS)
def test_dataset_names_use_the_prefix(layer) -> None:
    assert spec.dataset_id("spb", layer) == f"spb_{layer}"
