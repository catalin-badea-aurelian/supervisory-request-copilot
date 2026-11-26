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

    def __init__(
        self, settings: Settings, component: str = "validation", request_id: str = "none"
    ) -> None:
        self._settings = settings
        self._component = component
        self._request_id = request_id
        self._client = make_client(settings, impersonate=settings.copilot_sa)
        self.bytes_processed = 0

    def query(
        self, sql: str, params: dict[str, Any] | None = None, request_id: str | None = None
    ) -> pd.DataFrame:
        from google.cloud import bigquery

        config = query_job_config(self._settings, self._component, request_id or self._request_id)
        config.query_parameters = [
            bigquery.ScalarQueryParameter(k, "DATE", v)
            for k, v in (params or {}).items()
            if isinstance(v, date)
        ]
        job = self._client.query(sql, job_config=config)
        rows = job.result()
        self.bytes_processed += job.total_bytes_billed or 0
        return pd.DataFrame([dict(r.items()) for r in rows], columns=[f.name for f in rows.schema])


def _query_params(params: dict[str, Any]) -> list[Any]:
    from google.cloud import bigquery

    out = []
    for k, v in sorted(params.items()):
        if isinstance(v, date):
            out.append(bigquery.ScalarQueryParameter(k, "DATE", v))
        elif isinstance(v, bool):
            out.append(bigquery.ScalarQueryParameter(k, "BOOL", v))
        elif isinstance(v, int):
            out.append(bigquery.ScalarQueryParameter(k, "INT64", v))
        elif isinstance(v, str):
            out.append(bigquery.ScalarQueryParameter(k, "STRING", v))
        else:
            raise TypeError(f"unsupported query parameter type for {k}: {type(v).__name__}")
    return out


class BigQueryExecutor:
    """Showcase-mode executor (M3): dry run, maximum_bytes_billed, labels, timeout.

    Runs as the copilot identity (impersonation, no keys). Only GuardedBackend calls it.
    """

    dialect = "bigquery"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self._settings = settings
        self.client = client or make_client(settings, impersonate=settings.copilot_sa)

    def _config(self, params: dict[str, Any], labels: dict[str, str], **kw: Any) -> Any:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            maximum_bytes_billed=self._settings.max_bytes_billed,
            labels=labels,
            query_parameters=_query_params(params),
            **kw,
        )

    def dry_run(self, sql: str, params: dict[str, Any], labels: dict[str, str]) -> int | None:
        cfg = self._config(params, labels, dry_run=True, use_query_cache=False)
        job = self.client.query(sql, job_config=cfg)
        return int(job.total_bytes_processed or 0)

    def run(
        self, sql: str, params: dict[str, Any], labels: dict[str, str], timeout_s: float
    ) -> tuple[pd.DataFrame, int | None]:
        import concurrent.futures

        from copilot.data.guarded import QueryTimeout

        cfg = self._config(
            params, labels, use_query_cache=True, job_timeout_ms=int(timeout_s * 1000)
        )
        job = self.client.query(sql, job_config=cfg)
        try:
            rows = job.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            job.cancel()
            raise QueryTimeout(f"query exceeded {timeout_s}s") from None
        df = pd.DataFrame([dict(r.items()) for r in rows], columns=[f.name for f in rows.schema])
        return df, int(job.total_bytes_billed or 0)

    def describe(self, table_ref: str) -> list[dict[str, str]]:
        table = self.client.get_table(table_ref.replace("`", ""))
        return [{"name": f.name, "type": f.field_type} for f in table.schema]
