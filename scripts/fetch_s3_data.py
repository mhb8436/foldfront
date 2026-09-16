#!/usr/bin/env python3
"""Fetch RAPID run artifacts from NCP S3 into a local analysis directory.

This is the operational downloader used for paper-data refreshes. It requires
the private NCP S3 credentials from an environment file and is not copied into
the public release.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Iterable

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pipeline_mcp.s3 import ncp_storage


DEFAULT_RUN_PREFIXES = ("cath_train_", "cath_val_", "cath_test_")
DEFAULT_SUFFIXES = (
    "status.json",
    "summary.json",
    "request.json",
    "chain_strategy.json",
    "backbones.json",
    "conservation.json",
    "mask_consensus.json",
    "fixed_positions.json",
    "fixed_positions_check.json",
    "fixed_positions_consensus.json",
    "mutation_report.json",
    "proteinmpnn.json",
    "soluprot.json",
    "af2_scores.json",
    "relax_scores.json",
    "designs.fasta",
    "designs_filtered.fasta",
    "af2_selected.fasta",
    "target.fasta",
)


def _candidate_env_files(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            PROJECT_ROOT / "pipeline-mcp" / ".env",
            Path("/opt/protein_pipeline/pipeline-mcp/.env"),
        ]
    )
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def load_runtime_env(explicit: str | None = None) -> Path | None:
    for env_file in _candidate_env_files(explicit):
        if env_file.exists():
            load_dotenv(str(env_file), override=False)
            return env_file
    return None


def _run_id_from_key(key: str) -> str:
    parts = key.split("/")
    if len(parts) >= 2 and parts[0] == "outputs":
        return parts[1]
    return ""


def _matches_run_prefix(run_id: str, prefixes: Iterable[str]) -> bool:
    return any(run_id.startswith(prefix) for prefix in prefixes)


def _matches_suffix(key: str, suffixes: Iterable[str]) -> bool:
    return any(key.endswith(suffix) for suffix in suffixes)


def download_cath_batch(
    target_dir: str | Path = "cath_outputs_s3",
    *,
    run_prefixes: Iterable[str] = DEFAULT_RUN_PREFIXES,
    suffixes: Iterable[str] = DEFAULT_SUFFIXES,
    prefix: str = "outputs/",
    overwrite: bool = False,
    dry_run: bool = False,
    max_runs: int = 0,
) -> dict[str, object]:
    """Download selected CATH run artifacts from S3."""
    ncp_storage._ensure_initialized()
    client = ncp_storage._client
    bucket = ncp_storage.bucket
    if not client:
        raise RuntimeError("S3 credentials not found or invalid")

    target_root = Path(target_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    run_prefixes = tuple(str(item) for item in run_prefixes)
    suffixes = tuple(str(item) for item in suffixes)

    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    selected_runs: set[str] = set()
    seen_runs: set[str] = set()
    counts = Counter()
    downloaded_by_run = Counter()
    skipped_by_run = Counter()

    for page in pages:
        for obj in page.get("Contents", []) or []:
            key = str(obj.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            counts["objects_seen"] += 1
            run_id = _run_id_from_key(key)
            if not run_id or not _matches_run_prefix(run_id, run_prefixes):
                continue
            seen_runs.add(run_id)
            if max_runs > 0 and run_id not in selected_runs and len(selected_runs) >= max_runs:
                continue
            selected_runs.add(run_id)
            if not _matches_suffix(key, suffixes):
                continue

            relative = key.replace(prefix, "", 1) if key.startswith(prefix) else key
            local_path = target_root / relative
            counts["files_matched"] += 1
            if dry_run:
                print(f"[dry-run] {key} -> {local_path}")
                continue
            if local_path.exists() and not overwrite:
                skipped_by_run[run_id] += 1
                counts["files_skipped"] += 1
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading {key} -> {local_path}")
            client.download_file(bucket, key, str(local_path))
            downloaded_by_run[run_id] += 1
            counts["files_downloaded"] += 1

    return {
        "bucket": bucket,
        "prefix": prefix,
        "target_dir": str(target_root),
        "run_prefixes": list(run_prefixes),
        "runs_seen": len(seen_runs),
        "runs_selected": len(selected_runs),
        "selected_runs": sorted(selected_runs),
        "counts": dict(counts),
        "downloaded_by_run": dict(sorted(downloaded_by_run.items())),
        "skipped_by_run": dict(sorted(skipped_by_run.items())),
        "dry_run": bool(dry_run),
    }


def _parse_csv(text: str) -> list[str]:
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", default="cath_outputs_s3")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--prefix", default="outputs/")
    parser.add_argument(
        "--run-prefixes",
        default=",".join(DEFAULT_RUN_PREFIXES),
        help="Comma-separated run-id prefixes to download.",
    )
    parser.add_argument(
        "--suffixes",
        default=",".join(DEFAULT_SUFFIXES),
        help="Comma-separated filename suffixes to keep.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0)
    args = parser.parse_args(argv)

    env_file = load_runtime_env(args.env_file)
    print(f"env_file={env_file or 'not found'}")
    summary = download_cath_batch(
        args.target_dir,
        run_prefixes=_parse_csv(args.run_prefixes),
        suffixes=_parse_csv(args.suffixes),
        prefix=str(args.prefix),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        max_runs=int(args.max_runs),
    )
    print(
        "download summary: "
        f"runs_selected={summary['runs_selected']} "
        f"matched={summary['counts'].get('files_matched', 0)} "
        f"downloaded={summary['counts'].get('files_downloaded', 0)} "
        f"skipped={summary['counts'].get('files_skipped', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
