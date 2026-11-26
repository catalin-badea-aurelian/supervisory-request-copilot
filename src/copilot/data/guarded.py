"""Guarded data-access layer: the only path from the copilot to data (ARCHITECTURE section 7).

    SQL ──> SqlPolicy.check ──> dry run (BigQuery: bytes vs limit) ──> execute (timeout,
            labels, maximum_bytes_billed) ──> canonical result hash ──> QueryRecord

The policy runs before anything reaches a backend, so a rejected statement never becomes a
job. `GuardedBackend` satisfies the validation engine's `ReadBackend` protocol.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from copilot.data.hashing import canonical_hash
from copilot.data.policy import PolicyViolation, SqlPolicy, normalise_table_name


class BytesLimitExceeded(RuntimeError):
    def __init__(self, estimated: int, limit: int) -> None:
        super().__init__(f"dry run estimates {estimated:,} bytes; limit is {limit:,}")
        self.estimated, self.limit = estimated, limit


class QueryTimeout(RuntimeError):
    pass


class Executor(Protocol):
    """A raw backend adapter. Only GuardedBackend calls it."""

    dialect: str

    def dry_run(self, sql: str, params: dict[str, Any], labels: dict[str, str]) -> int | None: ...

    def run(
        self, sql: str, params: dict[str, Any], labels: dict[str, str], timeout_s: float
    ) -> tuple[pd.DataFrame, int | None]: ...

    def describe(self, table_ref: str) -> list[dict[str, str]]: ...


@dataclass(frozen=True)
class QueryRecord:
    sql: str
    sql_sha256: str
    params: dict[str, Any]
    snapshot_id: str
    component: str
    request_id: str
    estimated_bytes: int | None
    bytes_processed: int | None
    result_sha256: str
    rows: int
    duration_s: float


class GuardedBackend:
    def __init__(
        self,
        executor: Executor,
        policy: SqlPolicy,
        *,
        component: str,
        request_id: str,
        snapshot_id: str,
        max_bytes: int,
        timeout_s: float,
        labels_fn: Any = None,
    ) -> None:
        if policy.dialect != executor.dialect:
            raise ValueError("policy and executor dialects differ")
        self._ex = executor
        self.policy = policy
        self.dialect = executor.dialect
        self.component = component
        self.request_id = request_id
        self.snapshot_id = snapshot_id
        self.max_bytes = max_bytes
        self.timeout_s = timeout_s
        self._labels_fn = labels_fn
        self.records: list[QueryRecord] = []

    def labels(self) -> dict[str, str]:
        if self._labels_fn is not None:
            return dict(self._labels_fn(self.component, self.request_id))
        return {"component": self.component, "request_id": self.request_id}

    def execute(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> tuple[pd.DataFrame, QueryRecord]:
        params = dict(params or {})
        self.policy.check(sql)  # raises PolicyViolation before any backend call
        labels = self.labels()
        estimated = self._ex.dry_run(sql, params, labels)
        if estimated is not None and estimated > self.max_bytes:
            raise BytesLimitExceeded(estimated, self.max_bytes)
        start = time.monotonic()
        df, processed = self._ex.run(sql, params, labels, self.timeout_s)
        rec = QueryRecord(
            sql=sql,
            sql_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            params=params,
            snapshot_id=self.snapshot_id,
            component=self.component,
            request_id=self.request_id,
            estimated_bytes=estimated,
            bytes_processed=processed,
            result_sha256=canonical_hash(df),
            rows=len(df),
            duration_s=round(time.monotonic() - start, 3),
        )
        self.records.append(rec)
        return df, rec

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        return self.execute(sql, params)[0]

    def describe(self, table_ref: str) -> list[dict[str, str]]:
        if normalise_table_name(table_ref) not in self.policy.allowed:
            raise PolicyViolation("table_not_allowed", normalise_table_name(table_ref))
        return self._ex.describe(table_ref)
