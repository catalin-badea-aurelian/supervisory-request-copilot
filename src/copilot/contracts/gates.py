"""Human gates: what a gate shows (GatePayload) and what a human decided (GateDecision)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GateId = Literal["G1", "G2", "G3", "GD"]
GateKind = Literal["spec", "escalation", "agent_error", "budget", "signoff"]
RunStatus = Literal["IN_PROGRESS", "ON_HOLD", "REJECTED", "DELIVERED"]

# options per gate (ARCHITECTURE section 11)
G1_OPTIONS = ("approve", "request_changes")
G2_OPTIONS = ("independent_path", "hold", "reject")
G2_ERROR_OPTIONS = ("retry", "hold", "reject")
G3_OPTIONS = ("approve", "request_changes", "reject")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatePayload(_Strict):
    gate: GateId
    kind: GateKind
    summary: str
    options: list[str]
    required_acknowledgements: list[str] = Field(default_factory=list)  # WARN ids at G3
    details: dict[str, Any] = Field(default_factory=dict)


class GateDecision(_Strict):
    decision_id: str  # e.g. "REQ-1:G2:1"
    gate: GateId
    decision: str
    actor: str
    role: str
    comment: str | None = None
    acknowledged_warnings: list[str] = Field(default_factory=list)
    ts: datetime


class AcceptedFinding(_Strict):
    """A FAIL decided at G2: carried on later validations, never re-escalated."""

    key: str  # metric_id|check_id|canonical slices
    metric_id: str
    check_id: str
    decision_id: str
