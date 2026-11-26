"""`copilot rerun <id>` (ARCHITECTURE section 12.3): reproduce a run and compare hashes.

1. verify the audit chain
2. check the domain pack is the version the run used (content hash) and the snapshot is intact
3. re-execute every stored query in a guarded metric session on that snapshot
4. re-run validation, re-applying the accepted findings from the manifest
5. compare result hashes and the validation report hash

LLM steps are not re-run: the plan and the spec are taken from the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from copilot.audit.log import verify_chain
from copilot.audit.manifest import read_manifest
from copilot.contracts.analysis import AnalysisResult
from copilot.contracts.request import RequestSpec
from copilot.data.hashing import canonical_hash
from copilot.data.snapshots import ParquetSnapshot, snapshot_from_manifest
from copilot.workflow.graph import Runtime, validate_analysis


class RerunError(RuntimeError):
    pass


@dataclass
class HashRow:
    item: str
    expected: str
    actual: str

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


@dataclass
class RerunReport:
    request_id: str
    rows: list[HashRow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(r.ok for r in self.rows)


def rerun(rt: Runtime, request_id: str) -> RerunReport:
    run_dir = rt.run_dir(request_id)
    chain = verify_chain(run_dir)
    if not chain.ok:
        raise RerunError(f"audit chain broken at event {chain.first_bad_seq}: {chain.reason}")
    m = read_manifest(run_dir)
    if m.spec is None or m.snapshot is None or not m.query_runs:
        raise RerunError(f"{request_id} has no executed queries to reproduce (status {m.status})")
    if m.pack_content_sha256 != rt.pack.content_sha256:
        raise RerunError(
            f"domain pack differs from the run ({m.pack_id}@{m.pack_version}); check out the "
            "commit of that pack version and rerun"
        )
    snap = snapshot_from_manifest(m.snapshot)
    if isinstance(snap, ParquetSnapshot):
        snap.verify()
    spec = RequestSpec(**m.spec)
    report = RerunReport(request_id)
    results = {}
    session = rt.open_session(
        rt.settings,
        rt.pack,
        request_id=request_id,
        component="rerun",
        profile="metric",
        snapshot=snap,
    )
    try:
        for run in m.query_runs:
            params = {k: date.fromisoformat(v) for k, v in run.params.items()}
            df, _ = session.backend.execute(run.sql, params)
            results[run.result_ref] = df
            report.rows.append(
                HashRow(
                    f"{run.metric_id} ({run.source_path})", run.result_sha256, canonical_hash(df)
                )
            )
    finally:
        session.close()
    # validation runs on the re-executed results
    analysis = AnalysisResult(
        request_id=request_id, runs=m.query_runs, source_path=m.query_runs[0].source_path
    )
    validation = validate_analysis(rt, spec, analysis, m.snapshot, m.accepted_findings, results)
    report.rows.append(
        HashRow("validation report", m.validation_report_sha256 or "", validation.report_sha256)
    )
    return report
