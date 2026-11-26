"""`make demo`: the demo scenarios end to end with scripted answers.

Replay by default (recorded LLM responses in demo/recordings/<scenario>, no API key needed);
`--record` calls the API once and rewrites the recordings. After each scenario the audit
chain is verified and the run is reproduced with `rerun`.
"""

from __future__ import annotations

from copilot.audit.log import verify_chain
from copilot.config import Settings
from copilot.workflow import console as cons
from copilot.workflow.rerun import rerun
from copilot.workflow.runner import drive, new_request_id
from copilot.workflow.scripted import ANSWERS_DIR, Script


def run_demo(settings: Settings, scenarios: list[str], mode: str, backend: str | None) -> bool:
    ok = True
    for name in scenarios:
        path = ANSWERS_DIR / f"{name}.yaml"
        script = Script.load(path)
        rid = new_request_id()
        cons.console.rule(f"{name}: {script.request}")
        cons.save_run_config(
            settings,
            rid,
            {
                "mode": mode,
                "recordings": str(script.recordings),
                "backend": backend or settings.backend,
                "answers": str(path),
            },
        )
        rt = cons.build_runtime(settings, mode, script.recordings, backend)
        state = drive(
            rt,
            rid,
            cons.logging_answerer(script),
            raw_request=script.request,
            requester=script.requester,
        )
        cons.summary(state)
        chain = verify_chain(rt.run_dir(rid))
        cons.console.print(
            f"audit verify: {'OK' if chain.ok else 'BROKEN'} ({chain.events} events)"
        )
        report = None
        if state.get("analysis"):
            report = rerun(rt, rid)
            cons.show_rerun(report)
        expected = script.expected.get("status")
        if (
            (expected and state.get("status") != expected)
            or not chain.ok
            or (report is not None and not report.ok)
        ):
            cons.console.print(f"[red]{name}: expected {expected}, got {state.get('status')}[/red]")
            ok = False
    return ok
