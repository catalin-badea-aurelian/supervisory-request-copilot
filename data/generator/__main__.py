"""CLI: python -m data.generator --target duckdb|parquet [--no-defects] [--scale 0.02]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from copilot.config import REPO_ROOT, Settings
from data.generator import constants as C
from data.generator.build import ALL_DEFECTS, build_dataset
from data.generator.write import write_dataset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="data.generator")
    ap.add_argument("--target", choices=["duckdb", "parquet", "bigquery"], default="duckdb")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--no-defects", action="store_true", help="plant no defects (clean dataset)")
    ap.add_argument("--out", type=Path, default=None, help="Parquet output directory")
    args = ap.parse_args(argv)

    defects_on = not args.no_defects
    flavour = "default" if defects_on else "clean"
    out_dir = args.out or REPO_ROOT / "data" / "parquet" / flavour
    frames = build_dataset(args.seed, args.scale, ALL_DEFECTS if defects_on else ())
    manifest = write_dataset(frames, out_dir, args.seed, args.scale)
    print(f"wrote {len(manifest['tables'])} tables to {out_dir}")

    if args.target == "duckdb":
        from copilot.data.duckdb_backend import load_parquet_into_duckdb

        settings = Settings.from_env()
        db_path = settings.duckdb_path
        if not defects_on:
            db_path = db_path.with_name(db_path.stem + "_clean" + db_path.suffix)
        load_parquet_into_duckdb(out_dir, db_path)
        print(f"loaded DuckDB database {db_path}")
    elif args.target == "bigquery":
        print(
            "BigQuery loading lands with the showcase step (needs your GCP project).",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
