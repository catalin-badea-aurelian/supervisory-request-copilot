"""Start, resume and drive a run through its human gates.

The graph runs until it pauses at an interrupt (a question round or a gate). An `Answerer`
turns the interrupt payload into the resume value: interactive (terminal), scripted
(demo/answers/*.yaml), or a test double. Returning None leaves the run paused; it can be
resumed later with `copilot resume <id>` from the SQLite checkpoint.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from copilot.workflow.graph import Runtime, build_graph

Answerer = Callable[[dict[str, Any]], Any]


def new_request_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"req-{ts}-{secrets.token_hex(2)}"


@contextmanager
def checkpointer(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def _pending(graph: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    snap = graph.get_state(config)
    for task in snap.tasks:
        for intr in getattr(task, "interrupts", ()):
            return dict(intr.value)
    return None


def drive(
    rt: Runtime,
    request_id: str,
    answer: Answerer,
    *,
    raw_request: str | None = None,
    requester: str = "requester",
    checkpoint_path: Path | None = None,
    max_steps: int = 50,
) -> dict[str, Any]:
    """Run (or resume) until finished or the answerer returns None. Returns the final state
    plus `paused_at` (the pending interrupt payload, if any)."""
    db = checkpoint_path or rt.settings.runs_dir / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": request_id}, "recursion_limit": 200}
    with checkpointer(db) as saver:
        graph = build_graph(rt, saver)
        if raw_request is not None:
            graph.invoke(
                {"request_id": request_id, "raw_request": raw_request, "requester": requester},
                config,
            )
        for _ in range(max_steps):
            pending = _pending(graph, config)
            if pending is None:
                break
            value = answer(pending)
            if value is None:
                return {**graph.get_state(config).values, "paused_at": pending}
            graph.invoke(Command(resume=value), config)
        else:  # pragma: no cover - guard against a gate loop
            raise RuntimeError(f"{request_id}: more than {max_steps} human interactions")
        return {**graph.get_state(config).values, "paused_at": _pending(graph, config)}
