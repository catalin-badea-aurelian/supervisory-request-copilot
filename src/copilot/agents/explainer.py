"""Failure explainer (fast model): plain-language text for a failed check, shown at G2.

Its input is built by code:
- **check:** the failed check (id, name, measured values, threshold, affected slices)
- **derived:** figures computed and formatted by code, so the model never does arithmetic:
  observed, expected and difference per reconciliation line, the ratio (`2.00×`, or `n/a`
  when expected is 0), the line's share of its total gap, and percentage forms of relative
  differences and thresholds
- **related_findings:** other FAIL or WARN checks of the same metric on the same slice (same
  period and same dimension values), with their derived figures

Code checks the output:
- the number guard (`copilot.guards.numbers`): every number must come from that input
- the cause wording: `likely_cause` is either "cause not established" or starts with
  "likely cause, supported by <check id>" naming related findings

A failure of either gets one retry, then the explanation is marked unavailable. The text is
labelled as model-written, is stored outside the validation report (so the report hash stays
deterministic) and can never change a status.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from copilot.contracts.validation import CheckResult, ValidationReport
from copilot.guards.numbers import guard_message, invented_numbers
from copilot.llm.gateway import CallContext, Gateway

LABEL = "Explanation written by a language model from the check output; statuses are set by code."
NOT_ESTABLISHED = "cause not established"
_SLICE_IGNORE = {"status", "source", "line"}
_CAUSE = re.compile(
    r"^likely cause, supported by ((?:V-[A-Z]+-\d+)(?:(?:, | and )V-[A-Z]+-\d+)*)\b", re.IGNORECASE
)


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str  # one or two sentences
    likely_cause: str
    impact: str


# ---- formatting (code, never the model) -----------------------------------------------------
def fmt_amount(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() and abs(x) >= 1000 else f"{x:,.2f}"


def fmt_signed(x: float) -> str:
    return ("+" if x > 0 else "-" if x < 0 else "") + fmt_amount(abs(x))


def fmt_ratio(observed: float, expected: float) -> str:
    return "n/a" if expected == 0 else f"{observed / expected:.2f}×"


def fmt_pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def slice_identity(s: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The slice a finding is about: period and dimension values (not source or status)."""
    return tuple(sorted((k, str(v)) for k, v in s.items() if k not in _SLICE_IGNORE))


def _line_identity(line: dict[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """A reconciliation line as a slice (None for total and unallocated lines)."""
    name = str(line.get("line", ""))
    if name in ("total", "unallocated") or not name:
        return None
    return tuple(sorted({"merchant_country": name, "period": str(line.get("period"))}.items()))


def reconciliation_facts(
    lines: list[dict[str, Any]], only: set[tuple[tuple[str, str], ...]] | None = None
) -> list[dict[str, str]]:
    """Observed vs expected per line, the ratio, and each slice's share of its total gap.

    The share is the slice difference over the total difference of the same period and
    measure; it is signed, so a slice that offsets the gap shows a negative share, and it is
    `n/a` when the total reconciles (gap 0).
    """
    totals = {
        (str(t["period"]), str(t["measure"])): float(t["result"]) - float(t["ledger"])
        for t in lines
        if t.get("line") == "total"
    }
    out = []
    for ln in lines:
        observed, expected = float(ln["result"]), float(ln["ledger"])
        gap = observed - expected
        ident = _line_identity(ln)
        if only is not None and (ident is None or ident not in only):
            continue
        if ln.get("line") == "total" and gap == 0:
            continue  # reconciled totals add nothing to an explanation
        fact = {
            "line": str(ln.get("line")),
            "period": str(ln.get("period")),
            "measure": str(ln.get("measure")),
            "observed": fmt_amount(observed),
            "expected": fmt_amount(expected),
            "difference": fmt_signed(gap),
            "ratio": fmt_ratio(observed, expected),
        }
        if "rel_diff" in ln:
            fact["relative_difference"] = fmt_pct(float(ln["rel_diff"]))
        total_gap = totals.get((fact["period"], fact["measure"]))
        if ident is not None and total_gap is not None:
            fact["share_of_total_gap"] = "n/a" if total_gap == 0 else fmt_pct(gap / total_gap)
        out.append(fact)
    return out


def derived_facts(
    check: CheckResult, only: set[tuple[tuple[str, str], ...]] | None = None
) -> dict[str, Any]:
    m = check.measured
    facts: dict[str, Any] = {}
    if isinstance(m.get("lines"), list):
        facts["lines"] = reconciliation_facts(m["lines"], only)
    if isinstance(m.get("duplicate_keys"), dict):
        facts["duplicate_keys"] = {k: f"{int(v):,}" for k, v in sorted(m["duplicate_keys"].items())}
    for key, value in sorted(m.items()):
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if "rel_diff" in key or key.endswith(("_share", "_ratio_fraction")):
            facts[key] = fmt_pct(float(value))
    for key, value in sorted(check.threshold.items()):
        if isinstance(value, int | float) and not isinstance(value, bool) and key == "relative":
            facts["threshold_relative"] = fmt_pct(float(value))
    return facts


def related_findings(check: CheckResult, report: ValidationReport) -> list[dict[str, Any]]:
    """Other FAIL/WARN checks of the same metric on exactly the same slice(s)."""
    mine = {slice_identity(s) for s in check.affected_slices}
    out = []
    for other in report.checks:
        if other.check_id == check.check_id or other.metric_id != check.metric_id:
            continue
        if other.status == "PASS":
            continue
        shared = mine & {slice_identity(s) for s in other.affected_slices}
        if not shared:
            continue
        out.append(
            {
                "check_id": other.check_id,
                "check_name": other.check_name,
                "status": other.status,
                "slices": [dict(s) for s in sorted(shared)],
                "derived": derived_facts(other, shared),
            }
        )
    return sorted(out, key=lambda r: r["check_id"])


def explainer_input(check: CheckResult, report: ValidationReport) -> dict[str, Any]:
    return {
        "check": check.model_dump(
            mode="json",
            include={
                "check_id",
                "check_name",
                "metric_id",
                "status",
                "measured",
                "threshold",
                "affected_slices",
            },
        ),
        "derived": derived_facts(check),
        "related_findings": related_findings(check, report),
    }


def check_explanation(out: Explanation, data: dict[str, Any]) -> str | None:
    """Code checks on the output: numbers from the input only; cause wording and support."""
    invented = invented_numbers(out.model_dump(), data)
    problems = [guard_message(invented)] if invented else []
    cause = out.likely_cause.strip()
    related = {r["check_id"] for r in data.get("related_findings", [])}
    if cause.lower() != NOT_ESTABLISHED:
        m = _CAUSE.match(cause)
        cited = set(re.findall(r"V-[A-Z]+-\d+", m.group(1))) if m else set()
        if not m:
            problems.append(
                f'likely_cause must be "{NOT_ESTABLISHED}" or start with "likely cause, '
                'supported by <check id>:" naming a related finding'
            )
        elif not cited <= related:
            problems.append(
                f"likely_cause cites {sorted(cited - related)}, which are not related findings "
                f"on this slice (related: {sorted(related) or 'none'})"
            )
    return "\n".join(problems) or None


def explain_failure(
    gateway: Gateway, check: CheckResult, report: ValidationReport, ctx: CallContext
) -> Explanation:
    data = explainer_input(check, report)
    out, _ = gateway.call(
        "failure_explainer", data, Explanation, ctx, validate=lambda o: check_explanation(o, data)
    )
    return out
