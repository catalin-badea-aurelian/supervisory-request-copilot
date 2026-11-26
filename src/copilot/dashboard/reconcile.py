"""Pre-review control: every tile's figures are recomputed from independent sources.

`shown` is what the dashboard draws (the marts); `reference` is rebuilt from the governed
templates, the scan's audit log and the run folders. Any difference blocks gate GD.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from copilot.dashboard.data import Data
from copilot.dashboard.figures import tile_figures
from copilot.dashboard.spec import DashboardSpec
from copilot.packs.models import DomainPack

REL_TOL = 1e-9


@dataclass(frozen=True)
class Mismatch:
    tile_id: str
    key: str
    shown: Any
    reference: Any


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a is b or a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-12)
    return bool(a == b)


def diff_figures(tile_id: str, shown: dict[str, Any], reference: dict[str, Any]) -> list[Mismatch]:
    out = []
    for k in sorted(shown.keys() | reference.keys()):
        a, b = shown.get(k, "<missing>"), reference.get(k, "<missing>")
        if not _equal(a, b):
            out.append(Mismatch(tile_id, k, a, b))
    return out


def reconcile(
    spec: DashboardSpec, shown: Data, reference: Data, pack: DomainPack
) -> dict[str, Any]:
    tiles, mismatches = [], []
    for t in spec.tiles():
        a, b = tile_figures(t, shown, pack), tile_figures(t, reference, pack)
        diffs = diff_figures(t.id, a, b)
        mismatches += diffs
        tiles.append({"tile_id": t.id, "figures": len(a), "status": "MISMATCH" if diffs else "OK"})
    return {
        "ok": not mismatches,
        "tiles": tiles,
        "mismatches": [asdict(m) for m in mismatches[:200]],
        "mismatch_count": len(mismatches),
    }
