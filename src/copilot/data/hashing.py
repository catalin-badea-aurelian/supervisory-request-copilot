"""Canonical result hashing (ARCHITECTURE section 7.3).

Rows are sorted by all columns, floats and decimals rounded to a fixed precision, the
table is serialised to Arrow IPC with a normalised schema and hashed with SHA-256. The
same function is used for generated data, query results, reruns and tests, in both
backends. It never looks at file bytes, so it is stable across operating systems and
Parquet writer versions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

DEFAULT_PRECISION = 10


def _normalise_column(col: pa.ChunkedArray, precision: int) -> pa.Array:
    arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    t = arr.type
    if pa.types.is_floating(t):
        return pc.round(arr.cast(pa.float64()), precision)
    if pa.types.is_decimal(t):
        # decimals compare equal across backends regardless of declared scale
        return pc.round(arr.cast(pa.float64()), precision)
    if pa.types.is_integer(t):
        return arr.cast(pa.int64())
    if pa.types.is_timestamp(t):
        return arr.cast(pa.timestamp("us", tz="UTC")).cast(pa.string())
    if pa.types.is_boolean(t):
        return arr
    return arr.cast(pa.string())


def canonical_table(data: pd.DataFrame | pa.Table, precision: int = DEFAULT_PRECISION) -> pa.Table:
    """Return an order-independent, type-normalised Arrow table for hashing."""
    table = data if isinstance(data, pa.Table) else pa.Table.from_pandas(data, preserve_index=False)
    names = table.column_names
    normalised = pa.table({n: _normalise_column(table[n], precision) for n in names})
    if normalised.num_rows and names:
        normalised = normalised.sort_by([(n, "ascending") for n in names])
    return normalised


def canonical_hash(data: pd.DataFrame | pa.Table, precision: int = DEFAULT_PRECISION) -> str:
    """SHA-256 over the canonical Arrow IPC serialisation of a result."""
    table = canonical_table(data, precision)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Raw-bytes hash; informational only (same machine, same lockfile)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
