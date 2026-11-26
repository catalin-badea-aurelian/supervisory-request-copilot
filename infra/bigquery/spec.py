"""Declarative BigQuery environment: datasets, dataset-level access, tables.

Pure data, no GCP imports, so the plan can be printed and unit-tested offline.
Mirrors docs/ARCHITECTURE.md section 7.4 and docs/GCP_SETUP.md section 6.
"""

from __future__ import annotations

from dataclasses import dataclass

LAYERS = ("raw", "group", "control", "snapshots", "ops", "marts")
IDENTITIES = ("loader", "copilot", "looker")

# dataset layer -> {identity: dataset-level role}. Anything not listed gets no access.
ACCESS: dict[str, dict[str, str]] = {
    "raw": {"loader": "WRITER", "copilot": "READER"},
    "group": {"loader": "WRITER", "copilot": "READER"},
    "control": {"loader": "WRITER", "copilot": "READER"},
    "snapshots": {"copilot": "WRITER"},
    "ops": {"copilot": "WRITER", "looker": "READER"},
    "marts": {"copilot": "READER", "looker": "READER"},
}

# marts views read the source datasets through authorised-dataset access, so the Looker
# identity can query marts without any access to the sources.
AUTHORIZED_DATASETS: dict[str, tuple[str, ...]] = {"marts": ("raw", "group", "control")}

DESCRIPTIONS = {
    "raw": "SPB source tables and load log (synthetic data)",
    "group": "SPB parent-group data product (synthetic data)",
    "control": "SPB ledger control totals, prior submissions, EWI thresholds (synthetic data)",
    "snapshots": "Per-run table snapshots (expire automatically)",
    "ops": "Copilot run telemetry, check results, gate decisions",
    "marts": "Dashboard views generated from the domain pack",
}


@dataclass(frozen=True)
class TableSpec:
    layer: str
    name: str
    columns: tuple[tuple[str, str], ...]  # (column, BigQuery type)
    partition_field: str | None = None
    cluster: tuple[str, ...] = ()
    require_partition_filter: bool = False


def _cols(*pairs: str) -> tuple[tuple[str, str], ...]:
    return tuple((p.split(":")[0], p.split(":")[1]) for p in pairs)


TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "raw",
        "src_merchants",
        _cols(
            "merchant_id:STRING",
            "merchant_country:STRING",
            "mcc_group:STRING",
            "onboarding_date:DATE",
            "risk_tier:STRING",
            "status:STRING",
            "_loaded_at:TIMESTAMP",
        ),
    ),
    TableSpec(
        "raw",
        "src_transactions",
        _cols(
            "txn_id:STRING",
            "merchant_id:STRING",
            "txn_ts:TIMESTAMP",
            "amount_eur:NUMERIC",
            "payment_method:STRING",
            "status:STRING",
            "consumer_country:STRING",
            "_loaded_at:TIMESTAMP",
        ),
        partition_field="txn_ts",
        cluster=("merchant_id", "payment_method"),
        require_partition_filter=True,
    ),
    TableSpec(
        "raw",
        "src_chargebacks",
        _cols(
            "chargeback_id:STRING",
            "txn_id:STRING",
            "cb_ts:TIMESTAMP",
            "reason_group:STRING",
            "amount_eur:NUMERIC",
            "outcome:STRING",
            "_loaded_at:TIMESTAMP",
        ),
        partition_field="cb_ts",
        cluster=("txn_id",),
    ),
    TableSpec(
        "raw",
        "src_fraud_losses",
        _cols(
            "loss_id:STRING",
            "txn_id:STRING",
            "detected_ts:TIMESTAMP",
            "loss_eur:NUMERIC",
            "_loaded_at:TIMESTAMP",
        ),
    ),
    TableSpec(
        "raw",
        "meta_load_log",
        _cols(
            "table_name:STRING",
            "partition:STRING",
            "loaded_at:TIMESTAMP",
            "max_event_ts:TIMESTAMP",
            "row_count:INT64",
            "expected_row_count:INT64",
        ),
    ),
    TableSpec(
        "group",
        "grp_merchant_monthly",
        _cols(
            "month:DATE",
            "merchant_country:STRING",
            "mcc_group:STRING",
            "txn_count:INT64",
            "settled_volume_eur:NUMERIC",
            "cb_count:INT64",
            "cb_amount_eur:NUMERIC",
            "cb_rate:FLOAT64",
            "_loaded_at:TIMESTAMP",
        ),
    ),
    TableSpec(
        "control",
        "ledger_control_totals",
        _cols(
            "period_month:DATE",
            "merchant_country:STRING",
            "measure:STRING",
            "amount_eur:NUMERIC",
        ),
    ),
    TableSpec(
        "control",
        "prior_submissions",
        _cols(
            "submission_id:STRING",
            "submitted_on:DATE",
            "metric_id:STRING",
            "period:STRING",
            "merchant_country:STRING",
            "value:FLOAT64",
        ),
    ),
    TableSpec(
        "control",
        "ewi_thresholds",
        _cols(
            "metric_id:STRING",
            "direction:STRING",
            "amber:FLOAT64",
            "red:FLOAT64",
            "owner:STRING",
            "version:STRING",
        ),
    ),
)


def dataset_id(prefix: str, layer: str) -> str:
    return f"{prefix}_{layer}"


def identity_emails(project: str, loader_sa: str, copilot_sa: str) -> dict[str, str]:
    looker = f"spb-looker@{project}.iam.gserviceaccount.com"
    return {"loader": loader_sa, "copilot": copilot_sa, "looker": looker}


def render_plan(project: str, location: str, prefix: str, emails: dict[str, str]) -> str:
    """Human-readable plan of exactly what bq-setup would create."""
    lines = [f"Project: {project}   Location: {location}", "", "DATASETS (create if missing)"]
    for layer in LAYERS:
        lines.append(f"  {project}:{dataset_id(prefix, layer)}   {DESCRIPTIONS[layer]}")
    lines += ["", "DATASET-LEVEL ACCESS ENTRIES (added; existing entries are never removed)"]
    for layer in LAYERS:
        ds = dataset_id(prefix, layer)
        entries = ACCESS.get(layer, {})
        for ident in IDENTITIES:
            role = entries.get(ident, "-")
            lines.append(f"  {ds:<15} {ident:<8} {role:<7} {emails[ident] if role != '-' else ''}")
    lines += ["", "AUTHORISED DATASETS (marts views may read these sources)"]
    for viewer, sources in AUTHORIZED_DATASETS.items():
        for src in sources:
            lines.append(
                f"  {dataset_id(prefix, src)} authorises dataset {dataset_id(prefix, viewer)}"
            )
    lines += ["", "TABLES (create if missing)"]
    for t in TABLES:
        extra = []
        if t.partition_field:
            extra.append(f"partition DAY({t.partition_field})")
        if t.cluster:
            extra.append("cluster " + ", ".join(t.cluster))
        if t.require_partition_filter:
            extra.append("require_partition_filter")
        lines.append(f"  {dataset_id(prefix, t.layer)}.{t.name}   " + "; ".join(extra))
    lines += [
        "",
        "NOT touched: billing, project IAM, service accounts, any key. spb_ops tables and",
        "spb_marts views are created later (M8).",
    ]
    return "\n".join(lines)


# ---- Snapshot role (M3, ADR 014) ------------------------------------------------------------
# A custom role bound on spb_snapshots only, so the copilot can create table snapshots with an
# expiration and delete them, without OWNER on the dataset. bigquery.tables.create is needed to
# create a snapshot; it also lets the identity create an *ordinary* empty table there (a
# documented residual permission). It cannot update data in or delete such a table: the role
# has no bigquery.tables.updateData and no bigquery.tables.delete.
SNAPSHOT_ROLE_ID = "spbSnapshotManager"
SNAPSHOT_ROLE_TITLE = "SPB snapshot manager"
SNAPSHOT_ROLE_DESCRIPTION = (
    "Create, read and delete table snapshots; bound on spb_snapshots only (ADR 014)"
)
SNAPSHOT_ROLE_PERMISSIONS: tuple[str, ...] = (
    "bigquery.datasets.get",
    "bigquery.tables.create",
    "bigquery.tables.createSnapshot",
    "bigquery.tables.deleteSnapshot",
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "bigquery.tables.list",
)
# dataset layer -> {identity: custom role id}, bound with dataset-level IAM
DATASET_IAM_BINDINGS: dict[str, dict[str, str]] = {"snapshots": {"copilot": SNAPSHOT_ROLE_ID}}
