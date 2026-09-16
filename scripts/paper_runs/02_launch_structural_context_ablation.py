#!/usr/bin/env python3
"""Launch the 8-target RAPID structural-context ablation for the manuscript."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pipeline_mcp.cath_ops import _launch_helper


DEFAULT_ARMS = "single,bioemu,rfd3_single,rfd3_bioemu"


def _load_env(explicit: str | None = None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            PROJECT_ROOT / "pipeline-mcp" / ".env",
            Path("/opt/protein_pipeline/pipeline-mcp/.env"),
        ]
    )
    for env_file in candidates:
        if env_file.exists():
            load_dotenv(str(env_file), override=False)
            return env_file
    return None


def _manifest_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in (
        PROJECT_ROOT / "data" / "benchmark" / "results" / "rapid_target_manifest.csv",
        PROJECT_ROOT
        / "public_data"
        / "benchmark"
        / "results"
        / "rapid_target_manifest.csv",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("rapid_target_manifest.csv not found")


def _selected_structural_targets(manifest_path: Path) -> list[str]:
    rows = list(csv.DictReader(manifest_path.open("r", encoding="utf-8", newline="")))
    targets = [
        str(row.get("target") or "").strip()
        for row in rows
        if str(row.get("selected_for_structural_context") or "").strip().lower()
        == "true"
    ]
    return [target for target in targets if target]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default="")
    parser.add_argument("--arms", default=DEFAULT_ARMS)
    parser.add_argument("--replicates", default="1")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume-existing", action="store_true", default=True)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env_file = _load_env(args.env_file)
    os.environ["PIPELINE_OUTPUT_ROOT"] = str(Path(args.output_root).resolve())
    manifest = _manifest_path(args.manifest)
    targets = [
        item.strip()
        for item in str(args.targets or "").split(",")
        if item.strip()
    ] or _selected_structural_targets(manifest)
    if not targets:
        raise SystemExit("No structural-context targets selected")

    runner = PROJECT_ROOT / "scripts" / "benchmark" / "13_run_backbone_ensemble_ablation.py"
    command = [
        sys.executable,
        "-u",
        str(runner),
        "--targets",
        ",".join(targets),
        "--arms",
        str(args.arms),
        "--replicates",
        str(args.replicates),
    ]
    if args.force:
        command.append("--force")
    if args.resume_existing:
        command.append("--resume-existing")
    if args.stop_on_error:
        command.append("--stop-on-error")

    payload = {
        "env_file": str(env_file) if env_file else None,
        "manifest": str(manifest),
        "output_root": str(Path(args.output_root).resolve()),
        "targets": targets,
        "arms": str(args.arms).split(","),
        "replicates": str(args.replicates).split(","),
        "command": command,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **payload}, ensure_ascii=False, indent=2))
        return 0

    job = _launch_helper(
        str(Path(args.output_root).resolve()),
        kind="paper_structural_context_ablation",
        label=f"Paper structural-context ablation ({len(targets)} targets)",
        command=command,
        metadata=payload,
        cwd=str(PROJECT_ROOT),
    )
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
