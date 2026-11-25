"""Same seed gives identical content; different seeds differ."""

from pathlib import Path

import yaml

from data.generator.build import ALL_DEFECTS, build_dataset
from data.generator.write import write_dataset

FIXTURES = Path(__file__).parent / "fixtures"


def _manifest(tmp_path, seed, scale, defects, name):
    return write_dataset(build_dataset(seed, scale, defects), tmp_path / name, seed, scale)


def _canonical(tmp_path, seed, scale, defects, name):
    manifest = _manifest(tmp_path, seed, scale, defects, name)
    return {t: v["canonical_sha256"] for t, v in manifest["tables"].items()}


def test_same_seed_gives_identical_canonical_hashes(tmp_path):
    a = _canonical(tmp_path, 42, 0.03, ALL_DEFECTS, "a")
    b = _canonical(tmp_path, 42, 0.03, ALL_DEFECTS, "b")
    assert a == b


def test_same_machine_raw_file_hashes_match_informationally(tmp_path):
    m1 = _manifest(tmp_path, 42, 0.03, ALL_DEFECTS, "a")
    m2 = _manifest(tmp_path, 42, 0.03, ALL_DEFECTS, "b")
    # informational only: raw bytes are guaranteed on one machine / one lockfile, not across OSes
    assert {t: v["file_sha256_informational"] for t, v in m1["tables"].items()} == {
        t: v["file_sha256_informational"] for t, v in m2["tables"].items()
    }


def test_different_seed_changes_the_data(tmp_path):
    a = _canonical(tmp_path, 42, 0.03, (), "a")
    b = _canonical(tmp_path, 7, 0.03, (), "b")
    assert a["src_transactions"] != b["src_transactions"]


def test_golden_canonical_hashes_at_small_scale(tmp_path):
    """Detects accidental drift of the generated world (canonical, OS-independent hashes)."""
    golden = yaml.safe_load((FIXTURES / "data_hashes.yaml").read_text("utf-8"))
    got = {
        "default": _canonical(tmp_path, 42, 0.03, ALL_DEFECTS, "d"),
        "clean": _canonical(tmp_path, 42, 0.03, (), "c"),
    }
    assert got == golden["scale_0.03"]
