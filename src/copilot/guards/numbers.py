"""Number guard: model-written text may only contain numbers that code supplied.

Principle 3 says numbers never come from an LLM. Text a model writes about figures (the
failure explainer in M4, the narrative writer in M5) is checked here: every number in the
output must also appear in the input the model was given.

What counts as the same number (format normalisation):
- **thousands separators:** `1,793,696.26`, `1 793 696.26` (no-break or narrow no-break
  space) and `1793696.26` are the same value
- **trailing zeros:** `2`, `2.0` and `2.00` are the same value; values compare exactly, so a
  rounded number (`0.15` for `0.1512`) does not match
- **sign:** `+896,848.13`, `-896,848.13` and `896,848.13` match by absolute value, since prose
  says "over by X" rather than "+X"
- **ratios:** `×`, `x` and the word `times` are the same suffix, and a ratio is allowed
  only where the input holds a ratio of that value
- **percentages:** a `%` number is allowed only if the input holds that percentage in `%`
  form; code supplies them, the model never converts `0.151` to `15.1%` itself

What is not a number here:
- digits inside identifiers: check ids (`V-REC-01`), table names, `Q1`, `D1`
- dates and periods (`2025-08-01`, `2025-08`, `2025-Q3`); a year on its own (`August
  2025`) is allowed when the input contains a date in that year
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Kind = Literal["number", "ratio", "percent"]

_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b")  # V-REC-01, src_v2, bge-small
_PERIOD = re.compile(r"\b(\d{4})-(?:Q[1-4]|\d{2}(?:-\d{2})?)\b")
_SEP = ",  "
_NUMBER = re.compile(
    r"(?<![\w.])"  # not glued to a word or a preceding decimal point
    r"([+\-−]?)"
    rf"(\d{{1,3}}(?:[{_SEP}]\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:[  ]?(×|x|%)|[  ](times))?"
    r"(?![\w])"
)


@dataclass(frozen=True)
class Num:
    text: str
    value: Decimal  # absolute value
    kind: Kind


def _strip(text: str) -> tuple[str, set[int]]:
    years = {int(m.group(1)) for m in _PERIOD.finditer(text)}
    text = _PERIOD.sub(" ", text)
    text = _ID.sub(" ", text)
    return text, years


def extract(text: str) -> tuple[list[Num], set[int]]:
    """Numbers in a text, and the years of the dates and periods it contains."""
    cleaned, years = _strip(text)
    out = []
    for m in _NUMBER.finditer(cleaned):
        digits = re.sub(f"[{_SEP}]", "", m.group(2))
        try:
            value = Decimal(digits)
        except InvalidOperation:  # pragma: no cover - the regex only yields digits
            continue
        suffix = m.group(3)
        suffix = suffix or m.group(4)
        kind: Kind = (
            "percent" if suffix == "%" else "ratio" if suffix in ("×", "x", "times") else "number"
        )
        out.append(Num(m.group(0).strip(), value, kind))
    return out, years


def _as_text(source: Any) -> str:
    if isinstance(source, str):
        return source
    return json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class Allowed:
    numbers: frozenset[tuple[Kind, Decimal]]
    years: frozenset[int]

    @classmethod
    def from_inputs(cls, *sources: Any) -> Allowed:
        nums: set[tuple[Kind, Decimal]] = set()
        years: set[int] = set()
        for s in sources:
            found, ys = extract(_as_text(s))
            nums |= {(n.kind, n.value.normalize()) for n in found}
            years |= ys
        return cls(frozenset(nums), frozenset(years))

    def permits(self, n: Num) -> bool:
        key = (n.kind, n.value.normalize())
        if key in self.numbers:
            return True
        if n.kind == "number" and n.value == n.value.to_integral() and int(n.value) in self.years:
            return True
        return False


def invented_numbers(output: Any, *inputs: Any) -> list[str]:
    """The numbers in `output` that no input supplied, in order of appearance."""
    allowed = Allowed.from_inputs(*inputs)
    found, _ = extract(_as_text(output))
    return [n.text for n in found if not allowed.permits(n)]


def guard_message(invented: list[str]) -> str:
    return (
        "These numbers do not appear in the data you were given: "
        + ", ".join(invented)
        + ". Use only numbers from the data, copied as given (ratios and percentages only "
        "where the data provides them); do not compute, round or convert numbers."
    )
