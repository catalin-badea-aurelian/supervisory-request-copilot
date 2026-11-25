"""Download the BCBS 239 corpus into corpus/ (gitignored) for RAG ingestion.

Implemented in M5. Invoked via `poe corpus` / `make corpus`.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    print("'corpus' is not implemented until M5.", file=sys.stderr)
    raise SystemExit(1)
