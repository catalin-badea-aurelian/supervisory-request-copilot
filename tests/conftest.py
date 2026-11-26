"""Shared fixtures: generated datasets (small for speed, full for calibration)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from copilot.data.duckdb_backend import DuckDBBackend, load_parquet_into_duckdb
from copilot.packs.loader import load_pack
from data.generator.build import ALL_DEFECTS, build_dataset
from data.generator.write import write_dataset
from tests.reference.metrics import Frames, load_frames

FIXTURES = Path(__file__).parent / "fixtures"
SMALL_SCALE = 0.05


@dataclass
class Dataset:
    name: str
    parquet_dir: Path
    db_path: Path
    manifest: dict
    backend: DuckDBBackend
    frames: Frames


def _make(tmp_root: Path, name: str, scale: float, defects: tuple[str, ...]) -> Dataset:
    out = tmp_root / name
    manifest = write_dataset(build_dataset(42, scale, defects), out, 42, scale)
    db = tmp_root / f"{name}.duckdb"
    load_parquet_into_duckdb(out, db)
    return Dataset(name, out, db, manifest, DuckDBBackend(db), load_frames(out))


@pytest.fixture(scope="session")
def pack():
    return load_pack("payments")


@pytest.fixture(scope="session")
def expected_defects() -> dict:
    return yaml.safe_load((FIXTURES / "defects_expected.yaml").read_text("utf-8"))


@pytest.fixture(scope="session")
def small_default(tmp_path_factory) -> Dataset:
    return _make(tmp_path_factory.mktemp("small_default"), "d", SMALL_SCALE, ALL_DEFECTS)


@pytest.fixture(scope="session")
def small_clean(tmp_path_factory) -> Dataset:
    return _make(tmp_path_factory.mktemp("small_clean"), "c", SMALL_SCALE, ())


@pytest.fixture(scope="session")
def small_dataset(tmp_path_factory, small_clean, small_default):
    """Small datasets by name: 'clean', 'all' (every defect) or a single defect 'D1'..'D4'."""
    cache = {"clean": small_clean, "all": small_default}

    def get(name: str) -> Dataset:
        if name not in cache:
            cache[name] = _make(
                tmp_path_factory.mktemp(f"small_{name}"), name, SMALL_SCALE, (name,)
            )
        return cache[name]

    return get


@pytest.fixture(scope="session")
def full_clean(tmp_path_factory) -> Dataset:
    return _make(tmp_path_factory.mktemp("full_clean"), "c", 1.0, ())


@pytest.fixture(scope="session")
def full_default(tmp_path_factory) -> Dataset:
    return _make(tmp_path_factory.mktemp("full_default"), "d", 1.0, ALL_DEFECTS)


@pytest.fixture(autouse=True)
def _no_llm_api(monkeypatch):
    """Unit tests never call the LLM API: creating a real client fails the test."""
    import anthropic

    def refuse(*_a, **_k):
        raise AssertionError("a test tried to create an Anthropic API client")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
