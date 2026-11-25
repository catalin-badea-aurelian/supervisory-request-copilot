"""V-RERUN-01 Determinism: re-executing the primary query gives the identical result hash."""

from __future__ import annotations

from copilot.data.hashing import canonical_hash
from copilot.validation.checks.base import CheckContext, CheckSpec, Evidence, Outcome, register


def gather(ctx: CheckContext) -> Evidence:
    q = ctx.target.primary
    return Evidence(
        frames={
            "primary": ctx.target.primary_result,
            "rerun": ctx.backend.query(q.sql, q.params),
        },
        info={"sql_sha256": q.sql_sha256},
    )


def evaluate(ev: Evidence, thresholds: dict[str, float]) -> Outcome:
    a = canonical_hash(ev.frames["primary"])
    b = canonical_hash(ev.frames["rerun"])
    ok = a == b
    return Outcome(
        "PASS" if ok else "FAIL",
        {"result_sha256": a, "rerun_sha256": b, "sql_sha256": ev.info.get("sql_sha256")},
        [] if ok else [{"line": "result", "status": "FAIL"}],
    )


SPEC = register(CheckSpec("V-RERUN-01", "Determinism", (3,), "FAIL", gather, evaluate))
