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

    def add_entries(ds: bigquery.Dataset, entries: list[bigquery.AccessEntry]) -> None:
        current = {(e.role, e.entity_type, str(e.entity_id)) for e in ds.access_entries}
        new = [e for e in entries if (e.role, e.entity_type, str(e.entity_id)) not in current]
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
                datasets[src], [bigquery.AccessEntry(None, "dataset", {"dataset": viewer_ref})]
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
