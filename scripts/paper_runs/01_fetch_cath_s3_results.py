#!/usr/bin/env python3
"""Download completed CATH train/val artifacts from NCP S3 for paper refresh."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_s3_data import download_cath_batch
from fetch_s3_data import load_runtime_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-dir",
        default=str(PROJECT_ROOT / "cath_outputs_s3"),
        help="Local directory where S3 artifacts are mirrored.",
    )
    parser.add_argument(
        "--run-prefixes",
        default="cath_train_,cath_val_",
        help="Comma-separated run-id prefixes. Default fetches corrected train/val refresh runs.",
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0)
    args = parser.parse_args(argv)

    env_file = load_runtime_env(args.env_file)
    print(f"env_file={env_file or 'not found'}")
    run_prefixes = [
        item.strip()
        for item in str(args.run_prefixes or "").split(",")
        if item.strip()
    ]
    summary = download_cath_batch(
        args.target_dir,
        run_prefixes=run_prefixes,
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        max_runs=int(args.max_runs),
    )
    print(
        "CATH S3 fetch complete: "
        f"runs_selected={summary['runs_selected']} "
        f"matched={summary['counts'].get('files_matched', 0)} "
        f"downloaded={summary['counts'].get('files_downloaded', 0)} "
        f"skipped={summary['counts'].get('files_skipped', 0)}"
    )
    if summary["selected_runs"]:
        print("selected_runs=" + ",".join(summary["selected_runs"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
