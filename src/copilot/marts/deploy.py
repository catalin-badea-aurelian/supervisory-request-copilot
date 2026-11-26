"""`make views`: write the generated view SQL to `dashboards/views/` and deploy it to `spb_marts`.

Deployment runs as the developer's own ADC (the project owner), as `bq-setup` does
(ARCHITECTURE section 21.3): the copilot and Looker identities are READER on `spb_marts`.
Each `CREATE OR REPLACE VIEW` is a query job with `maximum_bytes_billed` and labels. Views
read the sources through the authorised-dataset entries `make bq-setup` created; this module
changes no access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.config import REPO_ROOT, Settings
from copilot.marts.views import generate, render_file, substitute
from copilot.packs.models import DomainPack

VIEWS_DIR = REPO_ROOT / "dashboards" / "views"


def write_files(pack: DomainPack, out_dir: Path = VIEWS_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    views = generate(pack)
    for old in out_dir.glob("*.sql"):
        if old.stem not in views:
            old.unlink()
    for name, sql in views.items():
        p = out_dir / f"{name}.sql"
        p.write_text(render_file(pack, name, sql), encoding="utf-8", newline="\n")
        written[name] = p
    return written


def ddl(pack: DomainPack, settings: Settings) -> dict[str, str]:
    project, prefix = settings.gcp_project_id, settings.dataset_prefix
    out = {}
    p = pack.pack
    for name, sql in generate(pack).items():
        body = substitute(sql, project, prefix)
        desc = f"Generated from domain pack {p.id}@{p.version}; do not edit (make views)."
        out[name] = (
            f"CREATE OR REPLACE VIEW `{project}.{prefix}_marts.{name}`\n"
            f"OPTIONS (description = '{desc}')\nAS\n{body}"
        )
    return out


def deploy(
    pack: DomainPack, settings: Settings, client: Any = None, dry_run: bool = False
) -> dict[str, int | None]:
    """Create or replace every view; returns the bytes a dry run of each view's query reads."""
    from google.cloud import bigquery

    from copilot.data.bigquery_clients import job_labels, make_client

    client = client or make_client(settings, impersonate=None)
    estimates: dict[str, int | None] = {}
    project, prefix = settings.gcp_project_id, settings.dataset_prefix
    for name, sql in ddl(pack, settings).items():
        if not dry_run:
            cfg = bigquery.QueryJobConfig(
                maximum_bytes_billed=settings.max_bytes_billed, labels=job_labels("views", name)
            )
            client.query(sql, job_config=cfg).result(timeout=120)
        probe = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, labels=job_labels("views", name)
        )
        try:
            job = client.query(
                f"SELECT * FROM `{project}.{prefix}_marts.{name}`"
                if not dry_run
                else substitute(generate(pack)[name], project, prefix),
                job_config=probe,
            )
            estimates[name] = job.total_bytes_processed
        except Exception:  # noqa: BLE001 - an ops view over a table that does not exist yet
            estimates[name] = None
    return estimates
