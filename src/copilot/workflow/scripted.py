"""Scripted human answers for the demo (`demo/answers/<scenario>.yaml`).

Answers are matched by question topic and gate id, never by wording, and every scripted
answer and decision is logged with actor `scripted-demo`. The script fails loudly when it
meets a question topic or gate it has no answer for, or when G3 would need acknowledgements
the script does not list: a demo never improvises a human decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from copilot.config import REPO_ROOT

ACTOR = "scripted-demo"
ANSWERS_DIR = REPO_ROOT / "demo" / "answers"
RECORDINGS_DIR = REPO_ROOT / "demo" / "recordings"


class ScriptError(RuntimeError):
    pass


@dataclass
class Script:
    scenario: str
    request: str
    requester: str
    answers: dict[str, str]
    gates: dict[str, list[dict[str, Any]]]
    expected: dict[str, Any] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Script:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            scenario=str(raw["scenario"]),
            request=" ".join(str(raw["request"]).split()),
            requester=str(raw.get("requester", "requester")),
            answers={str(k): " ".join(str(v).split()) for k, v in raw["answers"].items()},
            gates={str(k): list(v) for k, v in raw["gates"].items()},
            expected=dict(raw.get("expected", {})),
        )

    @property
    def recordings(self) -> Path:
        return RECORDINGS_DIR / self.scenario

    def __call__(self, pending: dict[str, Any]) -> Any:
        if pending.get("type") == "questions":
            out = []
            for q in pending["questions"]:
                topic = q["topic"]
                if topic not in self.answers:
                    raise ScriptError(
                        f"{self.scenario}: no scripted answer for topic {topic!r} ({q['text']})"
                    )
                out.append({"topic": topic, "answer": self.answers[topic], "actor": ACTOR})
            return out
        gate = str(pending.get("gate"))
        n = self.used.get(gate, 0)
        script = self.gates.get(gate, [])
        if n >= len(script):
            raise ScriptError(
                f"{self.scenario}: no scripted decision left for {gate}: {pending.get('summary')}"
            )
        self.used[gate] = n + 1
        d = dict(script[n])
        if d.get("decision") not in pending.get("options", []):
            raise ScriptError(
                f"{self.scenario}: {gate} decision {d.get('decision')!r} is not "
                f"an option ({pending.get('options')})"
            )
        if gate == "G3" and d.get("decision") == "approve":
            acks = sorted(d.get("acknowledged_warnings", []))
            required = sorted(pending.get("required_acknowledgements", []))
            if acks != required:
                raise ScriptError(
                    f"{self.scenario}: G3 requires acknowledging {required}, the "
                    f"script lists {acks}"
                )
        return {**d, "actor": ACTOR, "role": d.get("role", "approver")}
