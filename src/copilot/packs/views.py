"""What agents and the MCP server may see of the domain pack: definitions only.

The definition view never contains tolerances, check lists, EWI thresholds, control tables or
the load log. The maker (Intake, Analyst, MCP users) works from definitions; only the checker
reads thresholds (ARCHITECTURE section 13).
"""

from __future__ import annotations

from typing import Any

from copilot.packs.models import DomainPack

AS_OF_RULE = (
    "Business as-of date, applied in the governed SQL: transaction, chargeback, fraud-loss and "
    "group-product rows loaded after as_of (DATE(_loaded_at) > as_of) are excluded. Merchant "
    "reference data (country, MCC group) is joined as stored."
)


def metric_definition_view(pack: DomainPack, metric_id: str) -> dict[str, Any]:
    m = pack.metrics[metric_id]
    return {
        "metric_id": m.id,
        "name": m.name,
        "version": m.version,
        "status": m.status,
        "owner": m.owner,
        "definition": " ".join(m.definition.split()),
        "unit": m.unit,
        "numerator": m.numerator,
        "denominator": m.denominator,
        "attribution": m.attribution,
        "allowed_dimensions": m.allowed_dimensions,
        "preferred_source": m.preferred_source,
        "independent_sources": m.independent_sources,
        "limitations": m.limitations,
        "as_of": AS_OF_RULE,
    }


def glossary_context(pack: DomainPack) -> dict[str, Any]:
    """The whole approved glossary plus enumerated dimensions, in a stable order."""
    p = pack.pack
    return {
        "pack": f"{p.id}@{p.version}",
        "entity": p.entity,
        "data_range": {k: v.isoformat() for k, v in sorted(p.data_range.items())},
        "dimensions": {
            name: {"description": d.description, "values": d.values}
            for name, d in sorted(p.dimensions.items())
        },
        "time_dimensions": ["month", "quarter"],
        "metrics": [
            metric_definition_view(pack, mid)
            for mid, m in sorted(pack.metrics.items())
            if m.status == "approved"
        ],
    }
