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
    query_timeout_s: int = 120
    snapshot_expiration_days: int = 90
    parquet_dir: Path = REPO_ROOT / "data" / "parquet" / "default"
    model_main: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    llm_mode: str = "replay"  # replay | record | live (ADR 015)
    run_token_budget: int = 150_000
    usd_eur: float = 0.92
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    runs_dir: Path = REPO_ROOT / "runs"

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
            query_timeout_s=int(e.get("COPILOT_QUERY_TIMEOUT_S", "120")),
            snapshot_expiration_days=int(e.get("COPILOT_SNAPSHOT_EXPIRATION_DAYS", "90")),
            parquet_dir=Path(
                e.get("COPILOT_PARQUET_DIR", str(REPO_ROOT / "data" / "parquet" / "default"))
            ),
            model_main=e.get("COPILOT_MODEL_MAIN", "claude-sonnet-5-5"),
            model_fast=e.get("COPILOT_MODEL_FAST", "claude-haiku-5-5"),
            llm_mode=e.get("COPILOT_LLM_MODE", "replay"),
            run_token_budget=int(e.get("COPILOT_RUN_TOKEN_BUDGET", "150000")),
            usd_eur=float(e.get("COPILOT_USD_EUR", "0.92")),
            llm_timeout_s=float(e.get("COPILOT_LLM_TIMEOUT_S", "120")),
            llm_max_retries=int(e.get("COPILOT_LLM_MAX_RETRIES", "2")),
            runs_dir=Path(e.get("COPILOT_RUNS_DIR", str(REPO_ROOT / "runs"))),
        )
