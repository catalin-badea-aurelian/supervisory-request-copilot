"""Configuration loaded from environment variables (.env). No cloud calls here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    backend: str = "bigquery"
    duckdb_path: Path = REPO_ROOT / "data" / "spb.duckdb"
    gcp_project_id: str = ""
    bq_location: str = "EU"
    dataset_prefix: str = "spb"
    copilot_sa: str = ""
    loader_sa: str = ""
    max_bytes_billed: int = 1_000_000_000
    model_main: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"

    def dataset(self, layer: str) -> str:
        """Dataset name for a layer, e.g. 'raw' -> 'spb_raw'."""
        return f"{self.dataset_prefix}_{layer}"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or REPO_ROOT / ".env", override=False)
        e = os.environ
        return cls(
            backend=e.get("COPILOT_BACKEND", "bigquery"),
            duckdb_path=Path(e.get("COPILOT_DUCKDB_PATH", str(REPO_ROOT / "data" / "spb.duckdb"))),
            gcp_project_id=e.get("GCP_PROJECT_ID", ""),
            bq_location=e.get("BQ_LOCATION", "EU"),
            dataset_prefix=e.get("BQ_DATASET_PREFIX", "spb"),
            copilot_sa=e.get("COPILOT_SA", ""),
            loader_sa=e.get("LOADER_SA", ""),
            max_bytes_billed=int(e.get("COPILOT_MAX_BYTES_BILLED", "1000000000")),
            model_main=e.get("COPILOT_MODEL_MAIN", "claude-sonnet-5-5"),
            model_fast=e.get("COPILOT_MODEL_FAST", "claude-haiku-5-5"),
        )
