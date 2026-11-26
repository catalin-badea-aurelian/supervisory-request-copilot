"""Read the marts into frames: BigQuery (as the copilot identity) or DuckDB (local mode)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from copilot.config import Settings
from copilot.marts.views import VIEWS
from copilot.ops.schema import OPS_TABLES
from copilot.packs.models import DomainPack


def load_bigquery(settings: Settings, pack: DomainPack, request_id: str) -> dict[str, pd.DataFrame]:
    from copilot.data.bigquery_clients import make_client, query_job_config

    client = make_client(settings, impersonate=settings.copilot_sa)
    out = {}
    for name in VIEWS:
        sql = f"SELECT * FROM `{settings.gcp_project_id}.{settings.dataset_prefix}_marts.{name}`"
        cfg = query_job_config(settings, "dashboard", request_id)
        rows = client.query(sql, job_config=cfg).result()
        out[name] = pd.DataFrame(
            [dict(r.items()) for r in rows], columns=[f.name for f in rows.schema]
        )
    return out


def load_duckdb(settings: Settings, pack: DomainPack) -> dict[str, pd.DataFrame]:
    from copilot.marts.local import connect, query_view
    from copilot.ops.sink import LocalSink

    sink = LocalSink(settings.runs_dir / "ops")
    con = connect(settings.parquet_dir, {t: sink.read(t) for t in OPS_TABLES})
    return {name: query_view(con, pack, name) for name in VIEWS}


def load_frames(
    settings: Settings, pack: DomainPack, backend: str | None, request_id: str
) -> dict[str, pd.DataFrame]:
    if (backend or settings.backend) == "bigquery":
        return load_bigquery(settings, pack, request_id)
    return load_duckdb(settings, pack)


def frames_hash(frames: dict[str, pd.DataFrame]) -> str:
    """Hash of the data a dashboard was built from (the 'data snapshot hash' in its footer)."""
    h = hashlib.sha256()
    for name in sorted(frames):
        df = frames[name]
        df = df.sort_values(list(df.columns), key=lambda s: s.astype(str)).reset_index(drop=True)
        body = df.to_json(
            orient="records", date_format="iso", double_precision=9, default_handler=str
        )
        h.update(f"{name}\n{body}\n".encode())
    return h.hexdigest()


def scan_dir_for(settings: Settings, scan_run_id: str | None) -> Path:
    runs = settings.runs_dir
    if scan_run_id:
        return runs / scan_run_id
    scans = sorted(
        (p for p in runs.glob("scan-*") if (p / "audit.jsonl").is_file()), key=lambda p: p.name
    )
    if not scans:
        raise FileNotFoundError("no scan run found; run `copilot dq scan` first")
    return scans[-1]
