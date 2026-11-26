"""Load and validate a domain pack from disk."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from copilot.config import REPO_ROOT
from copilot.packs.models import DomainPack, MetricDef, PackDef

DEFAULT_PACKS_DIR = REPO_ROOT / "domain_packs"


class PackError(ValueError):
    pass


def _content_hash(root: Path) -> str:
    """Hash of every pack file (path + bytes), independent of OS line endings."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def load_pack(pack_id: str = "payments", packs_dir: Path | None = None) -> DomainPack:
    root = (packs_dir or DEFAULT_PACKS_DIR) / pack_id
    if not (root / "pack.yaml").is_file():
        raise PackError(f"domain pack not found: {root}")
    pack = PackDef.model_validate(yaml.safe_load((root / "pack.yaml").read_text("utf-8")))
    metrics: dict[str, MetricDef] = {}
    for path in sorted((root / "metrics").glob("*.yaml")):
        metric = MetricDef.model_validate(yaml.safe_load(path.read_text("utf-8")))
        if metric.id != path.stem:
            raise PackError(f"metric id {metric.id!r} does not match file name {path.name}")
        metrics[metric.id] = metric
    pk = DomainPack(root=str(root), pack=pack, metrics=metrics, content_sha256=_content_hash(root))
    validate_pack(pk)
    return pk


def validate_pack(pk: DomainPack) -> None:
    root = Path(pk.root)
    for m in pk.metrics.values():
        for t in (m.sql_template, m.independent_sql_template):
            if not (root / t).is_file():
                raise PackError(f"{m.id}: template not found: {t}")
        if m.sql_template == m.independent_sql_template:
            raise PackError(f"{m.id}: independent template must differ from the primary one")
        for src in [m.preferred_source, *m.independent_sources]:
            if src not in pk.pack.sources:
                raise PackError(f"{m.id}: unknown source {src}")
        for d in m.allowed_dimensions:
            if d != "period" and d not in pk.pack.dimensions:
                raise PackError(f"{m.id}: unknown dimension {d}")
        if "V-REC-01" in m.checks and m.reconciles_to is None:
            raise PackError(f"{m.id}: V-REC-01 needs reconciles_to")
        if "V-DEF-01" in m.checks and not m.precomputed_columns:
            raise PackError(f"{m.id}: V-DEF-01 needs precomputed_columns")
        if "V-MAT-01" in m.checks and not m.maturity_days:
            raise PackError(f"{m.id}: V-MAT-01 needs maturity_days")
        for pc in m.precomputed_columns:
            if pc.table not in pk.pack.sources:
                raise PackError(f"{m.id}: unknown precomputed table {pc.table}")
    for m in pk.metrics.values():
        for src in [m.preferred_source, *m.independent_sources]:
            sdef = pk.pack.sources[src]
            if sdef.checker_only or sdef.layer == "control":
                raise PackError(f"{m.id}: source {src} is checker-only")
    for name, sdef in pk.pack.sources.items():
        if (sdef.period_column is None) != (sdef.period_column_type is None):
            raise PackError(f"source {name}: period_column and period_column_type go together")
