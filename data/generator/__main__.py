"""CLI: python -m data.generator --target duckdb|parquet|bigquery [--no-defects] [--force]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from copilot.config import REPO_ROOT, Settings
from data.generator import constants as C
from data.generator.build import ALL_DEFECTS, build_dataset
from data.generator.write import write_dataset

REFUSAL = (
    "refusing to load the clean dataset into BigQuery: it would overwrite the demo dataset "
    "(spb_raw / spb_group / spb_control). Clean-data tests run on DuckDB. "
    "Re-run with --force if you really mean it."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="data.generator")
    ap.add_argument("--target", choices=["duckdb", "parquet", "bigquery"], default="duckdb")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--no-defects", action="store_true", help="plant no defects (clean dataset)")
    ap.add_argument(
        "--force", action="store_true", help="allow --no-defects with --target bigquery"
    )
    ap.add_argument("--out", type=Path, default=None, help="Parquet output directory")
    args = ap.parse_args(argv)

    defects_on = not args.no_defects
    if args.target == "bigquery" and not defects_on and not args.force:
        print(REFUSAL, file=sys.stderr)
        return 2
    if args.target == "bigquery" and args.scale != 1.0:
        print(
            "refusing to load a scaled-down dataset into BigQuery (use scale 1.0)", file=sys.stderr
        )
        return 2

    flavour = "default" if defects_on else "clean"
    out_dir = args.out or REPO_ROOT / "data" / "parquet" / flavour
    frames = build_dataset(args.seed, args.scale, ALL_DEFECTS if defects_on else ())
    manifest = write_dataset(frames, out_dir, args.seed, args.scale)
    print(f"wrote {len(manifest['tables'])} tables to {out_dir}")

    settings = Settings.from_env()
    if args.target == "duckdb":
        from copilot.data.duckdb_backend import load_parquet_into_duckdb

        db_path = settings.duckdb_path
        if not defects_on:
            db_path = db_path.with_name(db_path.stem + "_clean" + db_path.suffix)
        load_parquet_into_duckdb(out_dir, db_path)
        print(f"loaded DuckDB database {db_path}")
    elif args.target == "bigquery":
        from copilot.data.bigquery_loader import load_parquet_to_bigquery

        if not defects_on:
            print("WARNING: --force given; the clean dataset replaces the demo dataset.")
        load_parquet_to_bigquery(out_dir, settings)
    return 0


def data_clean_task(target: str = "bigquery", force: bool = False) -> None:
    """Entry point of `poe data-clean`."""
    argv = ["--target", target, "--no-defects"] + (["--force"] if force else [])
    code = main(argv)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    raise SystemExit(main())
