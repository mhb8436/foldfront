#!/usr/bin/env python3
"""Launch the structural-context ablation as a managed background job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from backbone_ensemble_ablation import DEFAULT_ARMS
from backbone_ensemble_ablation import DEFAULT_TARGETS
from backbone_ensemble_ablation import _parse_csv_list
from backbone_ensemble_ablation import _parse_int_list
from pipeline_mcp.cath_ops import _launch_helper


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the structural-context ablation in the background"
    )
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--replicates", default="1")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)

    targets = _parse_csv_list(args.targets, DEFAULT_TARGETS)
    arms = _parse_csv_list(args.arms, DEFAULT_ARMS)
    replicates = _parse_int_list(args.replicates, [1])
    runner = PROJECT_ROOT / "scripts" / "benchmark" / "13_run_backbone_ensemble_ablation.py"
    command = [
        sys.executable,
        "-u",
        str(runner),
        "--targets",
        ",".join(targets),
        "--arms",
        ",".join(arms),
        "--replicates",
        ",".join(str(rep) for rep in replicates),
    ]
    if args.force:
        command.append("--force")
    if args.resume_existing:
        command.append("--resume-existing")
    if args.stop_on_error:
        command.append("--stop-on-error")

    job = _launch_helper(
        str(Path(args.output_root).resolve()),
        kind="backbone_ensemble_ablation",
        label=f"Structural-context ablation ({len(targets)} targets)",
        command=command,
        metadata={
            "targets": targets,
            "arms": arms,
            "replicates": replicates,
            "force": bool(args.force),
            "resume_existing": bool(args.resume_existing),
            "stop_on_error": bool(args.stop_on_error),
        },
        cwd=str(PROJECT_ROOT),
    )
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
