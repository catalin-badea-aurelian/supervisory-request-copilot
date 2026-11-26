"""`make lookml`: write the generated LookML project to `looker/`.

Generated files are replaced; files the generator no longer produces are removed, except in
`looker/refinements/`, which belongs to people and is never touched.
"""

from __future__ import annotations

from pathlib import Path

from copilot.config import REPO_ROOT
from copilot.lookml.generate import generate
from copilot.packs.models import DomainPack

LOOKER_DIR = REPO_ROOT / "looker"
GENERATED_DIRS = ("views", "dashboards")
REFINEMENTS_README = """# Refinements

Hand-written LookML refinements (`view: +name { ... }`) go here. `make lookml` never writes
or deletes files in this folder; generated files elsewhere in `looker/` must not be edited.
"""


def write_project(pack: DomainPack, root: Path = LOOKER_DIR) -> list[Path]:
    files = generate(pack)
    for d in GENERATED_DIRS:
        for old in (root / d).glob("*"):
            if old.is_file() and old.relative_to(root).as_posix() not in files:
                old.unlink()
    written = []
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        written.append(p)
    ref = root / "refinements"
    ref.mkdir(parents=True, exist_ok=True)
    readme = ref / "README.md"
    if not readme.exists():
        readme.write_text(REFINEMENTS_README, encoding="utf-8", newline="\n")
    return written
