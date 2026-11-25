"""Load generated Parquet files into the BigQuery source datasets, as the loader identity.

Runs with impersonation of LOADER_SA (WRITER on spb_raw / spb_group / spb_control only).
Load jobs have no `maximum_bytes_billed` field; they carry the mandatory labels instead.
"""

from __future__ import annotations

from pathlib import Path

from copilot.config import Settings
from copilot.data.bigquery_clients import job_labels, make_client
from infra.bigquery import spec


def load_parquet_to_bigquery(parquet_dir: Path, settings: Settings) -> None:
    from google.cloud import bigquery

    if not settings.loader_sa:
        raise SystemExit("LOADER_SA is not configured")
    client = make_client(settings, impersonate=settings.loader_sa)
    for t in spec.TABLES:
        path = parquet_dir / f"{t.name}.parquet"
        dataset = spec.dataset_id(settings.dataset_prefix, t.layer)
        table_id = f"{settings.gcp_project_id}.{dataset}.{t.name}"
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            labels=job_labels("loader"),
        )
        with path.open("rb") as fh:
            job = client.load_table_from_file(fh, table_id, job_config=config)
        job.result()
        rows = client.get_table(table_id).num_rows
        print(f"loaded {t.layer}.{t.name}: {rows} rows")
