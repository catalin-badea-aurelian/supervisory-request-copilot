"""BigQuery clients with Application Default Credentials and service-account impersonation.

No JSON keys, ever: the developer's ADC is only the *source* identity; code runs as the
loader or copilot service account through impersonation. Every query job carries
`maximum_bytes_billed` and lowercase labels (`request_id`, `component`).
"""

from __future__ import annotations

import re
from typing import Any

from copilot.config import Settings

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_LABEL_RE = re.compile(r"[^a-z0-9_-]")


def job_labels(component: str, request_id: str = "none") -> dict[str, str]:
    """BigQuery label values: lowercase letters, digits, hyphen, underscore (max 63)."""
    return {
        "component": _LABEL_RE.sub("-", component.lower())[:63],
        "request_id": _LABEL_RE.sub("-", request_id.lower())[:63],
    }


def make_client(settings: Settings, impersonate: str | None = None) -> Any:
    """Client for the project. `impersonate` is a service-account email, or None for the
    developer's own ADC (only `bq-setup` and `views` run that way)."""
    import google.auth
    from google.auth import impersonated_credentials
    from google.cloud import bigquery

    source, _ = google.auth.default(scopes=SCOPES)
    creds = source
    if impersonate:
        creds = impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=impersonate,
            target_scopes=SCOPES,
            lifetime=3600,
        )
    return bigquery.Client(
        project=settings.gcp_project_id, credentials=creds, location=settings.bq_location
    )


def query_job_config(settings: Settings, component: str, request_id: str = "none") -> Any:
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(
        maximum_bytes_billed=settings.max_bytes_billed,
        labels=job_labels(component, request_id),
        use_query_cache=True,
    )
