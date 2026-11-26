"""Where ops rows go: BigQuery load jobs (showcase) or JSON Lines files (local mode).

BigQuery writes are batch load jobs as the copilot identity (impersonation, no keys), with
lowercase job labels. Load jobs scan nothing, so `maximum_bytes_billed` does not apply; the
mart views that read these tables are queried with it as usual.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from copilot.config import Settings
from copilot.ops.schema import OPS_TABLES, OpsTable

Mode = Literal["append", "replace"]


class OpsSink(Protocol):
    def write(self, table: str, rows: list[dict[str, Any]], mode: Mode, request_id: str) -> str:
        """Write rows; returns a short description of where they went."""
        ...


def _check(table: str, rows: list[dict[str, Any]]) -> OpsTable:
    spec = OPS_TABLES[table]
    allowed = set(spec.column_names)
    for r in rows:
        extra = set(r) - allowed
        if extra:
            raise ValueError(f"{table}: unknown columns {sorted(extra)}")
    return spec


class LocalSink:
    """`<runs_dir>/ops/<table>.jsonl`, UTF-8 with original characters, LF line endings."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, table: str) -> Path:
        return self.root / f"{table}.jsonl"

    def write(self, table: str, rows: list[dict[str, Any]], mode: Mode, request_id: str) -> str:
        _check(table, rows)
        p = self.path(table)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) + "\n" for r in rows
        )
        with p.open("a" if mode == "append" else "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return str(p)

    def read(self, table: str) -> list[dict[str, Any]]:
        p = self.path(table)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line]


class BigQuerySink:
    def __init__(self, settings: Settings, client: Any = None) -> None:
        from copilot.data.bigquery_clients import make_client

        self.settings = settings
        self.client = client or make_client(settings, impersonate=settings.copilot_sa)
        self.dataset = f"{settings.gcp_project_id}.{settings.dataset_prefix}_ops"

    def _ensure(self, spec: OpsTable) -> str:
        from google.cloud import bigquery

        ref = f"{self.dataset}.{spec.name}"
        table = bigquery.Table(ref, schema=_bq_schema(spec))
        if spec.partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=spec.partition_field
            )
        if spec.cluster:
            table.clustering_fields = list(spec.cluster)
        self.client.create_table(table, exists_ok=True)
        return ref

    def write(self, table: str, rows: list[dict[str, Any]], mode: Mode, request_id: str) -> str:
        from google.cloud import bigquery

        from copilot.data.bigquery_clients import job_labels

        spec = _check(table, rows)
        ref = self._ensure(spec)
        cfg = bigquery.LoadJobConfig(
            schema=_bq_schema(spec),
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=(
                bigquery.WriteDisposition.WRITE_APPEND
                if mode == "append"
                else bigquery.WriteDisposition.WRITE_TRUNCATE
            ),
            labels=job_labels("ops-export", request_id),
        )
        payload = [json.loads(json.dumps(r, default=str)) for r in rows]
        self.client.load_table_from_json(payload, ref, job_config=cfg).result(timeout=300)
        return ref


def _bq_schema(spec: OpsTable) -> list[Any]:
    from google.cloud import bigquery

    return [bigquery.SchemaField(c.name, c.type, mode=c.mode) for c in spec.columns]


def make_sink(settings: Settings, backend: str | None = None) -> OpsSink:
    kind = backend or settings.backend
    if kind == "bigquery":
        return BigQuerySink(settings)
    return LocalSink(settings.runs_dir / "ops")
