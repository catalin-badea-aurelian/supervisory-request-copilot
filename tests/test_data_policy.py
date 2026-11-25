"""Isolation of test-only assets: agents and tools must never read the defect knowledge."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
FORBIDDEN = (
    "data.generator",
    "defects_expected",
    "apply_d1",
    "apply_d2",
    "apply_d3",
    "tests.reference",
)


def test_package_never_references_defect_config_or_generator() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text("utf-8")
        offenders += [f"{path.relative_to(REPO)}: {tok}" for tok in FORBIDDEN if tok in text]
    assert not offenders, offenders


def test_defect_manifest_lives_under_tests_only() -> None:
    found = [
        p.relative_to(REPO).as_posix()
        for p in REPO.rglob("defects_expected*")
        if ".venv" not in p.parts
    ]
    assert found == ["tests/fixtures/defects_expected.yaml"]


def test_domain_pack_contains_no_defect_knowledge() -> None:
    pack_text = "\n".join(
        p.read_text("utf-8") for p in (REPO / "domain_packs").rglob("*") if p.is_file()
    ).lower()
    for token in ("planted", "defects_expected", "inject"):
        assert token not in pack_text, token


def test_generator_is_outside_the_installed_package() -> None:
    assert (REPO / "data" / "generator").is_dir()
    assert not (SRC / "copilot" / "generator").exists()
