"""`lookml-publish` and `lookml-check` need a Looker instance, which is optional (ADR 016).

They stay unimplemented on purpose: the LookML project is generated and unit-tested without an
instance, and these two steps run only when someone has one (docs/GCP_SETUP.md section 12).
Exits non-zero so a skipped step never looks like success.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    task = argv[0] if argv else "lookml"
    print(
        f"'{task}' requires a Looker (Google Cloud core) instance, which this project does not "
        "assume (ADR 016). The presentation layer is the agent-built dashboard: "
        "`copilot dashboard build --scan`, then `copilot gate GD`. See docs/GCP_SETUP.md "
        "section 12 for the optional Looker steps.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
