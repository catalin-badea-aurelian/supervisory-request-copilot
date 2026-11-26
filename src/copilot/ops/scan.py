"""Level 2 data-quality scan (ARCHITECTURE section 9.3): the M2 check catalogue without a request.

Per approved metric: one request over the pack's full data range, grain month, by merchant
country, on the preferred path. It runs through a guarded, snapshot-bound session with the
validation profile (M3), so it uses the same definitions, checks, policy and byte limits as a
Level 3 request. No LLM is involved.

Results are written to `spb_ops.ops_check_results` with `run_type='scan'` as a dense grid:
one row per metric x check x month x country. A cell takes the worst status of the slices
that cover it (a slice without a period or country covers all months or countries). A failed
check without slices fails every cell, and every other cell is PASS. `measured` and
`threshold` are kept on WARN and FAIL rows only.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from copilot.audit.log import AuditLog
from copilot.config import Settings
from copilot.contracts.validation import CheckResult, ValidationReport, worst
from copilot.packs.compiler import MetricRequest
from copilot.packs.models import DomainPack
from copilot.validation.runner import run_checks
from copilot.validation.target import prepare_target

SCAN_DIMS = ("period", "merchant_country")


@dataclass(frozen=True)
class ScanOutcome:
    run_id: str
    as_of: date
    reports: list[ValidationReport]
    rows: list[dict[str, Any]]
    snapshot_id: str

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for r in self.reports:
            for c in r.checks:
                out[c.status] += 1
        return out


def scan_months(pack: DomainPack) -> list[date]:
    start, end = pack.pack.data_range["start"], pack.pack.data_range["end"]
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def scan_request(pack: DomainPack, metric_id: str, as_of: date) -> MetricRequest:
    r = pack.pack.data_range
    return MetricRequest(metric_id, r["start"], r["end"], as_of, "month", SCAN_DIMS)


def _covers(s: dict[str, str], month: date, country: str) -> bool:
    period = s.get("period")
    if period is not None and period[:7] != month.isoformat()[:7]:
        return False
    c = s.get("merchant_country")
    return c is None or c == country


def cell_status(result: CheckResult, month: date, country: str) -> str:
    if not result.affected_slices:
        return result.status
    return worst([s["status"] for s in result.affected_slices if _covers(s, month, country)])


def grid_rows(
    result: CheckResult,
    *,
    run_id: str,
    as_of: date,
    pack: DomainPack,
    months: list[date],
    countries: list[str],
    recorded_at: str,
) -> list[dict[str, Any]]:
    metric = pack.metrics[result.metric_id]
    measured = json.dumps(result.measured, sort_keys=True, ensure_ascii=False)
    threshold = json.dumps(result.threshold, sort_keys=True, ensure_ascii=False)
    rows = []
    for m in months:
        for c in countries:
            status = cell_status(result, m, c)
            rows.append(
                {
                    "run_id": run_id,
                    "run_type": "scan",
                    "as_of": as_of.isoformat(),
                    "pack_version": pack.pack.version,
                    "metric_id": result.metric_id,
                    "metric_version": metric.version,
                    "check_id": result.check_id,
                    "check_name": result.check_name,
                    "principle_refs": list(result.principle_refs),
                    "status": status,
                    "measured": measured if status != "PASS" else None,
                    "threshold": threshold if status != "PASS" else None,
                    "period_month": m.isoformat(),
                    "merchant_country": c,
                    "accepted_ref": result.accepted_ref,
                    "recorded_at": recorded_at,
                }
            )
    return rows


def new_run_id(as_of: date) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"scan-{as_of.isoformat()}-{stamp}-{secrets.token_hex(2)}"


def run_scan(
    settings: Settings,
    pack: DomainPack,
    as_of: date,
    *,
    backend: str | None = None,
    metrics: list[str] | None = None,
    open_session: Any = None,
    run_id: str | None = None,
) -> ScanOutcome:
    from copilot.data.session import open_session as default_open

    opener = open_session or default_open
    rid = run_id or new_run_id(as_of)
    audit = AuditLog(settings.runs_dir / rid, rid)
    ids = metrics or sorted(m for m, d in pack.metrics.items() if d.status == "approved")
    audit.append(
        "scan_started",
        actor="dq-scan",
        role="checker",
        summary=f"as_of={as_of} metrics={len(ids)}",
        payload={"as_of": as_of.isoformat(), "metrics": ids, "pack": pack.pack.version},
    )
    session = opener(
        settings,
        pack,
        request_id=rid,
        component="dq-scan",
        profile="validation",
        backend=backend,
        snapshot_mode="new",
    )
    months = scan_months(pack)
    countries = list(pack.pack.dimensions["merchant_country"].values)
    recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
    reports, rows = [], []
    try:
        if session.snapshot is not None:
            audit.append(
                "snapshot_taken",
                actor="dq-scan",
                role="system",
                summary=session.snapshot_id,
                payload=session.snapshot.to_manifest(),
            )
        for mid in ids:
            req = scan_request(pack, mid, as_of)
            target = prepare_target(pack, req, session.backend, session.resolver, "preferred", rid)
            report = run_checks([target], pack, session.backend, session.resolver, rid)
            reports.append(report)
            for c in report.checks:
                audit.append(
                    "check_result",
                    actor="validation-engine",
                    role="checker",
                    summary=f"{c.metric_id} {c.check_id} {c.status}",
                    payload=c.model_dump(mode="json"),
                )
                rows += grid_rows(
                    c,
                    run_id=rid,
                    as_of=as_of,
                    pack=pack,
                    months=months,
                    countries=countries,
                    recorded_at=recorded_at,
                )
    finally:
        session.close()
    return ScanOutcome(rid, as_of, reports, rows, session.snapshot_id)
