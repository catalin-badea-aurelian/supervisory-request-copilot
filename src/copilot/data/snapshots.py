"""Run snapshots: freeze the physical state of every source table a run may read.

- BigQuery: one table snapshot per pack source in `spb_snapshots`, all cloned at the same
  instant (FOR SYSTEM_TIME AS OF the run timestamp), with an expiration. The snapshot names go
  into the run manifest, so a rerun inside the retention window reads exactly the same data.
- DuckDB (local mode): the Parquet files are immutable; the snapshot is the list of files
  with their SHA-256, and `snapshot_id` is the hash of that list. A run reads only these
  files, and a rerun fails fast if any of them changed.

Snapshot DDL is generated here from the pack, never from agent or user input, and is the
only statement besides SELECT the copilot identity ever sends (it is not routed through the
SELECT-only SqlPolicy on purpose).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from copilot.data.hashing import sha256_file
from copilot.packs.models import DomainPack

Resolver = Callable[[str], str]
_NAME_RE = re.compile(r"[^a-z0-9_]")


class SnapshotError(RuntimeError):
    pass


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- DuckDB / Parquet -----------------------------------------------------------------------
@dataclass(frozen=True)
class ParquetSnapshot:
    root: str
    files: dict[str, str]  # table -> file name (relative to root)
    sha256: dict[str, str]  # table -> file SHA-256
    snapshot_id: str

    @classmethod
    def take(cls, root: Path, tables: list[str]) -> ParquetSnapshot:
        files, hashes = {}, {}
        for t in sorted(tables):
            path = root / f"{t}.parquet"
            if not path.is_file():
                raise SnapshotError(f"missing Parquet file for {t}: {path}")
            files[t] = path.name
            hashes[t] = sha256_file(path)
        sid = "duckdb:" + _digest({t: hashes[t] for t in sorted(hashes)})
        return cls(str(root), files, hashes, sid)

    def path(self, table: str) -> Path:
        return Path(self.root) / self.files[table]

    def verify(self) -> None:
        """Fail fast if a file changed since the snapshot was taken (rerun)."""
        for t, expected in sorted(self.sha256.items()):
            path = self.path(t)
            if not path.is_file() or sha256_file(path) != expected:
                raise SnapshotError(f"snapshot {self.snapshot_id[:19]}…: {t} changed or missing")

    def resolver(self) -> Resolver:
        def resolve(name: str) -> str:
            if name not in self.files:
                raise SnapshotError(f"{name} is not in the snapshot")
            return name

        return resolve

    def to_manifest(self) -> dict[str, Any]:
        return {
            "kind": "duckdb",
            "root": self.root,
            "files": self.files,
            "sha256": self.sha256,
            "snapshot_id": self.snapshot_id,
        }


# ---- BigQuery -------------------------------------------------------------------------------
def snapshot_table_name(table: str, request_id: str) -> str:
    rid = _NAME_RE.sub("_", request_id.lower())
    name = f"{table}__{rid}"
    if len(name) > 1024:
        raise SnapshotError("snapshot name too long")
    return name


@dataclass(frozen=True)
class BigQuerySnapshot:
    project: str
    dataset: str
    run_ts: str  # ISO timestamp (UTC) the tables were cloned at
    expires_at: str
    mapping: dict[str, str]  # table -> snapshot table name (in `dataset`)
    sources: dict[str, str] = field(default_factory=dict)  # table -> fully qualified source
    snapshot_id: str = ""

    @classmethod
    def plan(
        cls,
        project: str,
        prefix: str,
        pack: DomainPack,
        request_id: str,
        run_ts: datetime,
        expiration_days: int,
    ) -> BigQuerySnapshot:
        mapping, sources = {}, {}
        for name, src in sorted(pack.pack.sources.items()):
            mapping[name] = snapshot_table_name(name, request_id)
            sources[name] = f"{project}.{prefix}_{src.layer}.{name}"
        ts = run_ts.replace(microsecond=0)
        expires = ts + timedelta(days=expiration_days)
        payload = {"project": project, "run_ts": ts.isoformat(), "mapping": mapping}
        return cls(
            project=project,
            dataset=f"{prefix}_snapshots",
            run_ts=ts.isoformat(),
            expires_at=expires.isoformat(),
            mapping=mapping,
            sources=sources,
            snapshot_id="bigquery:" + _digest(payload),
        )

    def fq(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{self.mapping[table]}"

    def ddl(self) -> list[str]:
        """One CREATE SNAPSHOT TABLE statement per source, all at the same instant."""
        out = []
        for t in sorted(self.mapping):
            out.append(
                f"CREATE SNAPSHOT TABLE `{self.fq(t)}`\n"
                f"CLONE `{self.sources[t]}`\n"
                f"FOR SYSTEM_TIME AS OF TIMESTAMP '{self.run_ts}'\n"
                f"OPTIONS (expiration_timestamp = TIMESTAMP '{self.expires_at}')"
            )
        return out

    def resolver(self) -> Resolver:
        def resolve(name: str) -> str:
            if name not in self.mapping:
                raise SnapshotError(f"{name} is not in the snapshot")
            return f"`{self.fq(name)}`"

        return resolve

    def to_manifest(self) -> dict[str, Any]:
        return {
            "kind": "bigquery",
            "project": self.project,
            "dataset": self.dataset,
            "run_ts": self.run_ts,
            "expires_at": self.expires_at,
            "mapping": self.mapping,
            "sources": self.sources,
            "snapshot_id": self.snapshot_id,
        }


def create_bigquery_snapshot(client: Any, snap: BigQuerySnapshot, labels: dict[str, str]) -> None:
    """Run the snapshot DDL as the copilot identity (needs the snapshot role on the dataset)."""
    from google.cloud import bigquery

    for stmt in snap.ddl():
        cfg = bigquery.QueryJobConfig(labels=labels)
        client.query(stmt, job_config=cfg).result()


def verify_bigquery_snapshot(client: Any, snap: BigQuerySnapshot) -> None:
    """Fail fast if a snapshot table is gone (expired or deleted) before a rerun."""
    from google.api_core.exceptions import NotFound

    for t in sorted(snap.mapping):
        try:
            client.get_table(snap.fq(t))
        except NotFound:
            raise SnapshotError(f"snapshot table {snap.fq(t)} no longer exists") from None


def snapshot_from_manifest(m: dict[str, Any]) -> ParquetSnapshot | BigQuerySnapshot:
    """Rebuild a stored run snapshot (rerun). The caller verifies it before use."""
    if m.get("kind") == "duckdb":
        return ParquetSnapshot(
            str(m["root"]), dict(m["files"]), dict(m["sha256"]), m["snapshot_id"]
        )
    if m.get("kind") == "bigquery":
        return BigQuerySnapshot(
            project=m["project"],
            dataset=m["dataset"],
            run_ts=m["run_ts"],
            expires_at=m["expires_at"],
            mapping=dict(m["mapping"]),
            sources=dict(m.get("sources", {})),
            snapshot_id=m["snapshot_id"],
        )
    raise SnapshotError(f"unknown snapshot kind {m.get('kind')!r}")
