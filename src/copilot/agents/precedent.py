"""Precedent search (code, not agent data access).

Earlier submissions are looked up in `prior_submissions` through a guarded validation-profile
session, but only their metadata (id, date, metric, period, number of countries) leaves this
module: submitted values never reach a prompt. Past copilot runs are matched from their
stored specs and shown at G1 only (they vary by machine, so they stay out of the prompt and
keep replay keys stable).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

PRECEDENT_SQL = (
    "SELECT metric_id, period, CAST(MIN(submitted_on) AS STRING) AS submitted_on, "
    "COUNT(DISTINCT submission_id) AS submissions, "
    "COUNT(DISTINCT merchant_country) AS countries, "
    "MIN(submission_id) AS first_id, MAX(submission_id) AS last_id "
    "FROM {table} GROUP BY metric_id, period"
)


@dataclass(frozen=True)
class Submission:
    """Earlier submissions of one metric for one quarter (metadata only)."""

    ref: str  # e.g. SUB:chargeback_rate_value:2025-Q1
    metric_id: str
    period: str  # e.g. 2025-Q1
    submitted_on: str
    submissions: int
    countries: int
    ids: str  # e.g. SUB-0018..SUB-0032


def quarter_of(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarters_between(start: date, end: date) -> list[str]:
    out, y, q = [], start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append(f"{y}-Q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def load_submissions(session: Any) -> list[Submission]:
    """Read submission metadata through a guarded session (validation profile)."""
    sql = PRECEDENT_SQL.format(table=session.resolver("prior_submissions"))
    df, _ = session.backend.execute(sql, {})
    rows = [
        Submission(
            ref=f"SUB:{r.metric_id}:{r.period}",
            metric_id=str(r.metric_id),
            period=str(r.period),
            submitted_on=str(r.submitted_on)[:10],
            submissions=int(r.submissions),
            countries=int(r.countries),
            ids=f"{r.first_id}..{r.last_id}",
        )
        for r in df.itertuples(index=False)
    ]
    return sorted(rows, key=lambda s: (s.period, s.metric_id))


def submission_index(submissions: list[Submission]) -> list[dict[str, Any]]:
    """What the Intake prompt sees: metadata only, in a stable order."""
    return [asdict(s) for s in submissions]


def overlapping(
    submissions: list[Submission], metric_ids: list[str], start: date, end: date
) -> list[Submission]:
    qs = set(quarters_between(start, end))
    return [s for s in submissions if s.period in qs and s.metric_id in metric_ids]


def past_runs(runs_dir: Path, metric_ids: list[str], exclude: str) -> list[dict[str, Any]]:
    """Earlier copilot runs whose confirmed spec shares a metric (shown at G1)."""
    out = []
    for spec_file in sorted(runs_dir.glob("*/spec.json")):
        if spec_file.parent.name == exclude:
            continue
        try:
            spec = json.loads(spec_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if set(spec.get("metrics", [])) & set(metric_ids):
            out.append(
                {
                    "request_id": spec.get("request_id"),
                    "title": spec.get("title"),
                    "metrics": spec.get("metrics"),
                    "period": spec.get("period"),
                }
            )
    return out
