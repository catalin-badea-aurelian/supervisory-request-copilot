"""Run manifest (ARCHITECTURE section 12.2): everything needed to rerun and compare.

runs/<request_id>/manifest.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from copilot.contracts.analysis import QueryRun
from copilot.contracts.gates import AcceptedFinding, GateDecision


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: str
    spec_sha256: str | None = None
    spec: dict[str, Any] | None = None
    pack_id: str
    pack_version: str
    pack_content_sha256: str
    backend: str
    snapshot: dict[str, Any] | None = None
    source_path: str = "preferred"
    query_runs: list[QueryRun] = Field(default_factory=list)
    validation_report_sha256: str | None = None
    accepted_findings: list[AcceptedFinding] = Field(default_factory=list)
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    llm: dict[str, Any] = Field(default_factory=dict)  # calls, tokens, cost (from the budget)
    audit_head_sha256: str | None = None


def write_manifest(run_dir: Path, manifest: RunManifest) -> Path:
    path = run_dir / "manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def read_manifest(run_dir: Path) -> RunManifest:
    return RunManifest.model_validate_json((run_dir / "manifest.json").read_text(encoding="utf-8"))
