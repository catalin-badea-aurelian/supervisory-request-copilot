"""Open a guarded, snapshot-bound data session for one run or one tool call.

    session = open_session(settings, pack, request_id="REQ-1", component="analyst")
    q = compile_metric(pack, req, "preferred", session.dialect, session.resolver)
    df, record = session.backend.execute(q.sql, q.params)

Templates compile straight against the snapshot names (through `resolver`), so no SQL is
rewritten after compilation. The allowlist holds exactly the resolved names of the tables
the profile may read:

- `metric`: raw and group sources (templates, MCP, Analyst): no control tables
- `validation`: every pack source, including the control tables (ledger, load log,
  prior submissions, thresholds) the checker needs
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from copilot.config import Settings
from copilot.data.guarded import GuardedBackend
from copilot.data.policy import SqlPolicy
from copilot.data.snapshots import BigQuerySnapshot, ParquetSnapshot
from copilot.packs.models import DomainPack

Profile = Literal["metric", "validation"]
SnapshotMode = Literal["new", "live"]


@dataclass
class DataSession:
    backend: GuardedBackend
    resolver: Callable[[str], str]
    snapshot: Any  # ParquetSnapshot | BigQuerySnapshot | None (live)
    snapshot_id: str
    profile: Profile

    @property
    def dialect(self) -> str:
        return self.backend.dialect

    def close(self) -> None:
        close = getattr(self.backend._ex, "close", None)  # noqa: SLF001
        if close:
            close()


def profile_tables(pack: DomainPack, profile: Profile) -> list[str]:
    if profile == "validation":
        return sorted(pack.pack.sources)
    return sorted(n for n, s in pack.pack.sources.items() if s.layer in ("raw", "group"))


def _policy(dialect: str, resolver: Callable[[str], str], tables: list[str]) -> SqlPolicy:
    return SqlPolicy(dialect, [resolver(t) for t in tables])


def open_session(
    settings: Settings,
    pack: DomainPack,
    *,
    request_id: str,
    component: str,
    profile: Profile = "metric",
    backend: str | None = None,
    snapshot: Any = None,
    snapshot_mode: SnapshotMode = "new",
    client: Any = None,
) -> DataSession:
    """`snapshot` reuses a stored snapshot (rerun); otherwise one is taken (or `live`)."""
    kind = backend or settings.backend
    tables = profile_tables(pack, profile)
    if kind == "duckdb":
        from copilot.data.duckdb_backend import ParquetExecutor

        snap = snapshot or ParquetSnapshot.take(settings.parquet_dir, sorted(pack.pack.sources))
        resolver = snap.resolver()
        executor: Any = ParquetExecutor(snap)
        sid = snap.snapshot_id
        labels_fn = None
    elif kind == "bigquery":
        from copilot.data.bigquery_backend import BigQueryExecutor, source_resolver
        from copilot.data.bigquery_clients import job_labels
        from copilot.data.snapshots import create_bigquery_snapshot, verify_bigquery_snapshot

        executor = BigQueryExecutor(settings, client)
        labels_fn = job_labels
        if snapshot is not None:
            verify_bigquery_snapshot(executor.client, snapshot)
            snap = snapshot
        elif snapshot_mode == "new":
            # one minute back: the clone instant must not be ahead of the server clock
            run_ts = datetime.now(UTC) - timedelta(minutes=1)
            snap = BigQuerySnapshot.plan(
                settings.gcp_project_id,
                settings.dataset_prefix,
                pack,
                request_id,
                run_ts,
                settings.snapshot_expiration_days,
            )
            create_bigquery_snapshot(executor.client, snap, job_labels("snapshot", request_id))
        else:
            snap = None
        if snap is not None:
            resolver = snap.resolver()
            sid = snap.snapshot_id
        else:
            resolver = source_resolver(settings, pack)
            sid = "live@" + datetime.now(UTC).replace(microsecond=0).isoformat()
    else:
        raise ValueError(f"unknown backend {kind!r}")
    guarded = GuardedBackend(
        executor,
        _policy(executor.dialect, resolver, tables),
        component=component,
        request_id=request_id,
        snapshot_id=sid,
        max_bytes=settings.max_bytes_billed,
        timeout_s=settings.query_timeout_s,
        labels_fn=labels_fn,
    )
    return DataSession(guarded, resolver, snap, sid, profile)
