"""bq-setup: create the EU datasets, dataset-level access and tables (run once, as the owner).

Default is a DRY RUN that only prints the plan and makes no call to Google Cloud.
Pass --apply to execute. Idempotent: existing datasets/tables are kept, access entries are
only ever added (never removed), and nothing outside BigQuery datasets is touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `infra`

from copilot.config import Settings  # noqa: E402
from infra.bigquery import spec  # noqa: E402


def _require(settings: Settings) -> None:
    missing = [
        n
        for n, v in (
            ("GCP_PROJECT_ID", settings.gcp_project_id),
            ("LOADER_SA", settings.loader_sa),
            ("COPILOT_SA", settings.copilot_sa),
        )
        if not v or "<" in v
    ]
    if missing:
        raise SystemExit(f"missing configuration in .env: {', '.join(missing)}")
    if settings.bq_location != "EU":
        raise SystemExit("BQ_LOCATION must be EU (data residency)")


def clear_sandbox_expirations(client, datasets) -> None:
    """Without billing, BigQuery runs in sandbox mode and forces a 60-day expiration on every
    table and partition, which silently deletes our 2024-2025 partitions. Clear it; if billing
    is not enabled, stop with a clear message (billing is the owner's decision)."""
    from google.api_core.exceptions import Forbidden

    for ds in datasets.values():
        if ds.default_table_expiration_ms is None and ds.default_partition_expiration_ms is None:
            continue
        ds.default_table_expiration_ms = None
        ds.default_partition_expiration_ms = None
        try:
            client.update_dataset(
                ds, ["default_table_expiration_ms", "default_partition_expiration_ms"]
            )
        except Forbidden as exc:
            raise SystemExit(
                f"{ds.dataset_id} has a default expiration that cannot be cleared: {exc.message}\n"
                "Link a billing account to the project (the free trial is enough), then re-run."
            ) from None
        print(f"cleared default expirations on {ds.dataset_id}")


def apply(settings: Settings) -> None:
    from google.api_core.exceptions import Conflict
    from google.cloud import bigquery

    from copilot.data.bigquery_clients import make_client

    client = make_client(settings)  # developer ADC: the project owner
    project, prefix = settings.gcp_project_id, settings.dataset_prefix
    emails = spec.identity_emails(project, settings.loader_sa, settings.copilot_sa)

    def ensure_dataset(layer: str) -> bigquery.Dataset:
        ref = bigquery.Dataset(f"{project}.{spec.dataset_id(prefix, layer)}")
        ref.location = settings.bq_location
        ref.description = spec.DESCRIPTIONS[layer]
        try:
            ds = client.create_dataset(ref)
            print(f"created dataset {ds.dataset_id}")
        except Conflict:
            ds = client.get_dataset(ref.reference)
            if ds.location != settings.bq_location:
                raise SystemExit(f"{ds.dataset_id} exists in {ds.location}, expected EU") from None
            print(f"exists  dataset {ds.dataset_id}")
        return ds

    datasets = {layer: ensure_dataset(layer) for layer in spec.LAYERS}
    clear_sandbox_expirations(client, datasets)

    def add_entries(ds: bigquery.Dataset, entries: list[bigquery.AccessEntry]) -> None:
        ds = client.get_dataset(ds.reference)  # fresh etag: earlier updates changed the dataset

        def key(e: bigquery.AccessEntry) -> tuple:
            if e.entity_type == "dataset":  # authorised dataset: identify by the viewer dataset
                return ("dataset", e.entity_id["dataset"]["datasetId"])
            return (e.role, e.entity_type, str(e.entity_id).lower())

        current = {key(e) for e in ds.access_entries}
        new = [e for e in entries if key(e) not in current]
        if new:
            ds.access_entries = [*ds.access_entries, *new]
            client.update_dataset(ds, ["access_entries"])
            print(f"access  {ds.dataset_id}: +{len(new)} entr{'y' if len(new) == 1 else 'ies'}")

    for layer, roles in spec.ACCESS.items():
        add_entries(
            datasets[layer],
            [bigquery.AccessEntry(role, "userByEmail", emails[i]) for i, role in roles.items()],
        )
    for viewer, sources in spec.AUTHORIZED_DATASETS.items():
        viewer_ref = datasets[viewer].reference.to_api_repr()
        for src in sources:
            add_entries(
                datasets[src],
                [
                    bigquery.AccessEntry(
                        None, "dataset", {"dataset": viewer_ref, "target_types": ["VIEWS"]}
                    )
                ],
            )

    for t in spec.TABLES:
        schema = [bigquery.SchemaField(n, ty) for n, ty in t.columns]
        table = bigquery.Table(f"{project}.{spec.dataset_id(prefix, t.layer)}.{t.name}", schema)
        if t.partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=t.partition_field
            )
        if t.cluster:
            table.clustering_fields = list(t.cluster)
        if t.require_partition_filter:
            table.require_partition_filter = True
        client.create_table(table, exists_ok=True)
        live = client.get_table(table.reference)
        fields = []
        if live.expires is not None:
            live.expires = None
            fields.append("expires")
        if live.time_partitioning and live.time_partitioning.expiration_ms is not None:
            live.time_partitioning.expiration_ms = None
            fields.append("time_partitioning")
        if fields:
            client.update_table(live, fields)
        print(f"table   {t.layer}.{t.name} ready")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bq-setup")
    ap.add_argument("--apply", action="store_true", help="execute (default: print the plan only)")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    _require(settings)
    emails = spec.identity_emails(settings.gcp_project_id, settings.loader_sa, settings.copilot_sa)
    print(
        spec.render_plan(
            settings.gcp_project_id, settings.bq_location, settings.dataset_prefix, emails
        )
    )
    if not args.apply:
        print("\nDRY RUN: nothing was created. Re-run with --apply to execute.")
        return 0
    print()
    apply(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
