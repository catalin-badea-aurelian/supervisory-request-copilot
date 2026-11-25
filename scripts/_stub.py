"""Loud placeholder for copilot commands not yet implemented.

Each poe/make task that has no real implementation yet calls this with its
own task name and target milestone, e.g. `python scripts/_stub.py data M1`.
It always exits non-zero so a missing milestone can never look like success.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _stub.py <task-name> <milestone> [...ignored args]", file=sys.stderr)
        return 1
    task, milestone = argv[0], argv[1]
    print(f"'{task}' is not implemented until {milestone}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
