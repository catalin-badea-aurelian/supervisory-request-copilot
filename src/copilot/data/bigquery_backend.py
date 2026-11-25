"""BigQuery read-only backend (showcase mode), running as the copilot identity.

Minimal for M1 (golden tests): dry-run byte check, `maximum_bytes_billed`, labels, named
DATE parameters. The sqlglot policy, timeouts and snapshots are added in M3.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from copilot.config import Settings
from copilot.data.bigquery_clients import make_client, query_job_config
from copilot.packs.models import DomainPack


def source_resolver(settings: Settings, pack: DomainPack):
    """Fully qualified, backtick-quoted table reference for a pack source."""

    def resolve(name: str) -> str:
        layer = pack.pack.sources[name].layer
        dataset = f"{settings.dataset_prefix}_{layer}"
        return f"`{settings.gcp_project_id}.{dataset}.{name}`"

    return resolve


class BigQueryBackend:
    dialect = "bigquery"

    def __init__(self, settings: Settings, component: str = "validation") -> None:
        self._settings = settings
        self._component = component
        self._client = make_client(settings, impersonate=settings.copilot_sa)
        self.bytes_processed = 0

    def query(
        self, sql: str, params: dict[str, Any] | None = None, request_id: str = "none"
    ) -> pd.DataFrame:
        from google.cloud import bigquery

        config = query_job_config(self._settings, self._component, request_id)
        config.query_parameters = [
            bigquery.ScalarQueryParameter(k, "DATE", v)
            for k, v in (params or {}).items()
            if isinstance(v, date)
        ]
        job = self._client.query(sql, job_config=config)
        rows = job.result()
        self.bytes_processed += job.total_bytes_billed or 0
        return pd.DataFrame([dict(r.items()) for r in rows], columns=[f.name for f in rows.schema])
