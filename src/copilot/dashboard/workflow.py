"""Dashboard build and gate GD (ADR 016).

`build` writes the spec to `dashboards/specs/<id>.yaml`, renders a candidate to
`runs/<id>/dashboard/index.html`, reconciles every tile against independent sources and logs
it all. Nothing reaches `docs/dashboard/` until `decide_gd` records an approval, and an
approval is refused when the reconciliation found a mismatch or the candidate changed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from copilot.audit.log import AuditLog, verify_chain
from copilot.config import REPO_ROOT, Settings
from copilot.dashboard.build import apply_llm_captions, build_spec
from copilot.dashboard.data import MartData, ReferenceData
from copilot.dashboard.load import frames_hash, load_frames, scan_dir_for
from copilot.dashboard.reconcile import reconcile
from copilot.dashboard.render import render_html
from copilot.dashboard.spec import dump_yaml, spec_hash, validate_spec
from copilot.packs.models import DomainPack

SPECS_DIR = REPO_ROOT / "dashboards" / "specs"
APPROVED_DIR = REPO_ROOT / "docs" / "dashboard"


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    dashboard_id: str
    status: str  # PENDING_GD or BLOCKED
    spec_path: Path
    html_path: Path
    reconciliation: dict[str, Any]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_dir(settings: Settings, dashboard_id: str) -> Path:
    return settings.runs_dir / dashboard_id


def _scan_as_of(scan_dir: Path, pack: DomainPack) -> date:
    for line in (scan_dir / "audit.jsonl").read_text("utf-8").splitlines():
        e = json.loads(line)
        if e["event_type"] == "scan_started":
            p = json.loads((scan_dir / e["payload_ref"]).read_text("utf-8"))
            return date.fromisoformat(p["as_of"])
    return pack.pack.default_as_of


def build_dashboard(
    settings: Settings,
    pack: DomainPack,
    *,
    scan_run_id: str | None = None,
    backend: str | None = None,
    specs_dir: Path = SPECS_DIR,
    captions: tuple[Any, Any] | None = None,
    open_session: Any = None,
    frames: dict[str, Any] | None = None,
) -> BuildResult:
    """`captions` is (gateway, call context) for the optional language-model captions."""
    from copilot.data.session import open_session as default_open

    scan_dir = scan_dir_for(settings, scan_run_id)
    as_of = _scan_as_of(scan_dir, pack)
    did = f"dash-{scan_dir.name}"
    spec = build_spec(pack, did, scan_dir.name, as_of.isoformat())
    if captions is not None:
        spec = apply_llm_captions(spec, pack, *captions)
    errors = validate_spec(spec, pack)
    if errors:
        raise ValueError("; ".join(errors))

    kind = backend or settings.backend
    frames = frames if frames is not None else load_frames(settings, pack, kind, did)
    shown = MartData(frames, pack)
    data_sha = frames_hash(frames)
    s_sha = spec_hash(spec)
    page = render_html(spec, shown, pack, spec_sha256=s_sha, data_sha256=data_sha)

    session = (open_session or default_open)(
        settings,
        pack,
        request_id=did,
        component="dashboard",
        profile="metric",
        backend=kind,
        snapshot_mode="live" if kind == "bigquery" else "new",
    )
    try:
        ref = ReferenceData(
            pack, session.backend, session.resolver, scan_dir, settings.runs_dir, as_of
        )
        report = reconcile(spec, shown, ref, pack)
    finally:
        session.close()

    out = run_dir(settings, did)
    (out / "dashboard").mkdir(parents=True, exist_ok=True)
    html_path = out / "dashboard" / "index.html"
    html_path.write_text(page, encoding="utf-8", newline="\n")
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / f"{did}.yaml"
    spec_path.write_text(dump_yaml(spec), encoding="utf-8", newline="\n")
    (out / "reconciliation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    status = "PENDING_GD" if report["ok"] else "BLOCKED"
    meta = {
        "dashboard_id": did,
        "status": status,
        "scan_run_id": scan_dir.name,
        "spec_sha256": s_sha,
        "data_sha256": data_sha,
        "html_sha256": _sha(page),
        "caption_source": spec.caption_source,
        "pack_version": pack.pack.version,
        "as_of": as_of.isoformat(),
        "backend": kind,
    }
    audit = AuditLog(out, did)
    audit.append(
        "dashboard_built",
        actor="dashboard-builder",
        role="maker",
        summary=f"{did} spec={s_sha[:12]} data={data_sha[:12]}",
        payload=meta,
    )
    audit.append(
        "reconciliation_completed",
        actor="dashboard-reconciler",
        role="checker",
        summary=f"{'OK' if report['ok'] else 'BLOCKED'}: {report['mismatch_count']} mismatches "
        f"over {len(report['tiles'])} tiles",
        payload=report,
    )
    (out / "dashboard.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    return BuildResult(did, status, spec_path, html_path, report)


def _evidence_md(meta: dict[str, Any], d: dict[str, Any], rep: dict[str, Any], head: str) -> str:
    rows = "\n".join(f"| {t['tile_id']} | {t['figures']} | {t['status']} |" for t in rep["tiles"])
    return f"""# Dashboard evidence

Synthetic data — fictional entity. Demonstration only.

- Dashboard: `{meta["dashboard_id"]}` (scan run `{meta["scan_run_id"]}`, as-of {meta["as_of"]})
- Pack: {meta["pack_version"]} · backend: {meta["backend"]} · captions: {meta["caption_source"]}
- Spec sha256: `{meta["spec_sha256"]}`
- Data sha256: `{meta["data_sha256"]}`
- Page sha256: `{meta["html_sha256"]}`
- Gate GD: **{d["decision"]}** by {d["actor"]} ({d["role"]}), reason: {d["reason"]}
- Audit head sha256: `{head}`

## Tile reconciliation (every tile recomputed from independent sources)

| tile | figures | status |
|---|---|---|
{rows}
"""


def decide_gd(
    settings: Settings,
    dashboard_id: str,
    decision: str,
    reason: str,
    *,
    actor: str = "approver",
    role: str = "approver",
    approved_dir: Path = APPROVED_DIR,
) -> dict[str, Any]:
    if decision not in ("approve", "reject"):
        raise GateError("decision must be approve or reject")
    if not reason.strip():
        raise GateError("a reason is required")
    out = run_dir(settings, dashboard_id)
    meta_path = out / "dashboard.json"
    if not meta_path.is_file():
        raise GateError(f"no built dashboard {dashboard_id!r}; run `copilot dashboard build`")
    meta = json.loads(meta_path.read_text("utf-8"))
    if meta["status"] in ("APPROVED", "REJECTED"):
        raise GateError(f"{dashboard_id} was already decided: {meta['status']}")
    chain = verify_chain(out)
    if not chain.ok:
        raise GateError(f"audit chain broken: {chain.reason}")
    rep = json.loads((out / "reconciliation.json").read_text("utf-8"))
    html_path = out / "dashboard" / "index.html"
    if decision == "approve":
        if meta["status"] == "BLOCKED" or not rep["ok"]:
            raise GateError(
                f"blocked: {rep['mismatch_count']} tile mismatches; fix the data and rebuild"
            )
        if _sha(html_path.read_text("utf-8")) != meta["html_sha256"]:
            raise GateError("the candidate changed after reconciliation; rebuild it")
    audit = AuditLog(out, dashboard_id)
    d = {
        "gate": "GD",
        "decision": decision,
        "actor": actor,
        "role": role,
        "reason": reason,
        "subject": "dashboard",
        "spec_sha256": meta["spec_sha256"],
        "html_sha256": meta["html_sha256"],
    }
    audit.append(
        "gate_decision", actor=actor, role=role, summary=f"GD {decision} {dashboard_id}", payload=d
    )
    meta["status"] = "APPROVED" if decision == "approve" else "REJECTED"
    meta["gd_reason"] = reason
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), "utf-8", newline="\n")
    if decision == "approve":
        approved_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(html_path, approved_dir / "index.html")
        head = verify_chain(out).head
        (approved_dir / "evidence.md").write_text(
            _evidence_md(meta, d, rep, head), encoding="utf-8", newline="\n"
        )
        (approved_dir / "manifest.json").write_text(
            json.dumps({**meta, "audit_head_sha256": head}, indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
    return meta
