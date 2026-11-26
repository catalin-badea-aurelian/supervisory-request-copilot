"""Minimal G3 summary (M4): runs/<id>/delivery.md. The full evidence pack replaces it in M5.

Every number comes from the stored result files or the validation report; text written by a
model is labelled as such.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from copilot.agents.explainer import LABEL
from copilot.contracts.analysis import AnalysisResult
from copilot.contracts.gates import AcceptedFinding
from copilot.contracts.request import RequestSpec
from copilot.contracts.validation import ValidationReport

MAX_TABLE_ROWS = 60


def _table(df: pd.DataFrame) -> str:
    shown = df.head(MAX_TABLE_ROWS)
    cols = list(shown.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in shown.itertuples(index=False):
        cells = [f"{v:.6g}" if isinstance(v, float) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    if len(df) > MAX_TABLE_ROWS:
        lines.append(f"\n_{len(df) - MAX_TABLE_ROWS} more rows in the result file._")
    return "\n".join(lines)


def issue_note(spec: RequestSpec, report: ValidationReport, accepted: list[AcceptedFinding]) -> str:
    """Drafted issue note to the data owner for findings accepted at G2 (code template)."""
    keys = {(a.metric_id, a.check_id) for a in accepted}
    rows = [c for c in report.checks if (c.metric_id, c.check_id) in keys]
    lines = [
        f"**To:** owner of the affected source(s)  \n**Re:** {spec.request_id}: data-quality "
        "findings accepted as documented limitations",
        "",
        "The following checks failed on the preferred source. The figures were delivered from "
        "the independent path; please investigate and confirm a correction date.",
        "",
    ]
    for c in rows:
        slices = "; ".join(
            ", ".join(f"{k}={v}" for k, v in s.items() if k != "status") for s in c.affected_slices
        )
        lines.append(f"- {c.check_id} {c.check_name} ({c.metric_id}): {slices or 'all slices'}")
    return "\n".join(lines)


def build_delivery(
    run_dir: Path,
    spec: RequestSpec,
    analysis: AnalysisResult,
    report: ValidationReport,
    accepted: list[AcceptedFinding],
    explanations: dict[str, Any],
    review: dict[str, Any],
) -> Path:
    out: list[str] = [
        f"# {spec.title}",
        "",
        f"Request `{spec.request_id}` · requester: {spec.requester} · as-of {spec.as_of_date} · "
        f"period {spec.period.start} to {spec.period.end} ({spec.period.grain}) · source path: "
        f"{analysis.source_path}",
        "",
        "## Results",
    ]
    for run in analysis.runs:
        df = pd.read_parquet(run_dir / run.result_ref)
        out += [
            "",
            f"### {run.metric_id} v{run.metric_version}",
            f"Template `{run.template_id}` · rows {run.rows} · result sha256 `{run.result_sha256}`",
            "",
            _table(df),
        ]
    out += [
        "",
        "## Validation",
        "",
        "| check | metric | status | accepted at G2 |",
        "|---|---|---|---|",
    ]
    for c in report.checks:
        out.append(f"| {c.check_id} | {c.metric_id} | {c.status} | {c.accepted_ref or ''} |")
    out += ["", f"Report sha256 `{report.report_sha256}`"]
    if explanations:
        out += ["", "## Failure explanations", "", f"_{LABEL}_", ""]
        for key, e in sorted(explanations.items()):
            out.append(f"- **{key}**: {e['summary']} Likely cause: {e['likely_cause']}")
    if accepted:
        out += ["", "## Accepted findings and issue note", "", issue_note(spec, report, accepted)]
    out += [
        "",
        "## Review",
        "",
        f"BCBS 239 review: {review.get('status', 'not assessed')} ({review.get('note', '')}).",
        "",
    ]
    path = run_dir / "delivery.md"
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return path
