#!/usr/bin/env python3
"""Launch BioEmu RMSD-gate sensitivity reruns for failed ablation arms."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
BENCHMARK_SRC = PROJECT_ROOT / "scripts" / "benchmark"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))
if str(BENCHMARK_SRC) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_SRC))

from backbone_ensemble_ablation import build_request
from backbone_ensemble_ablation import _target_to_pdb_path
from pipeline_mcp.app import build_runner
from pipeline_mcp.cath_ops import _launch_helper


DEFAULT_TARGETS = "1h6wA03,2auaB01,3jvoG00,3twkA01"
DEFAULT_ARMS = "bioemu,rfd3_bioemu"
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark" / "results"


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _load_env(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
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
            load_dotenv(str(env_file), override=True)
            return env_file
    return None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")


def _run_id(prefix: str, target: str, arm: str, replicate: int) -> str:
    return _safe_id(f"{prefix}_{target}_{arm}_s{int(replicate)}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _manifest(
    *,
    targets: list[str],
    arms: list[str],
    replicate: int,
    run_prefix: str,
    bioemu_num_samples: int,
    bioemu_max_attempted_structures: int,
    bioemu_max_return_structures: int,
    bioemu_target_rmsd_cutoff: float,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for target in targets:
        for arm in arms:
            jobs.append(
                {
                    "target": target,
                    "arm": arm,
                    "replicate": int(replicate),
                    "run_id": _run_id(run_prefix, target, arm, replicate),
                    "pdb_path": str(_target_to_pdb_path(target)),
                    "bioemu_num_samples": int(bioemu_num_samples),
                    "bioemu_max_attempted_structures": int(
                        bioemu_max_attempted_structures
                    ),
                    "bioemu_max_return_structures": int(
                        bioemu_max_return_structures
                    ),
                    "bioemu_target_rmsd_cutoff": float(bioemu_target_rmsd_cutoff),
                }
            )
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": (
            "Sensitivity rerun for BioEmu-containing structural-context arms "
            "that failed the predefined target-RMSD gate in the primary ablation."
        ),
        "targets": targets,
        "arms": arms,
        "replicate": int(replicate),
        "run_prefix": run_prefix,
        "jobs": jobs,
    }


def run_now(args: argparse.Namespace) -> int:
    env_file = _load_env(args.env_file)
    os.environ["PIPELINE_OUTPUT_ROOT"] = str(Path(args.output_root).resolve())
    targets = _parse_csv(args.targets)
    arms = _parse_csv(args.arms)
    run_prefix = str(args.run_prefix).strip()
    replicate = int(args.replicate)

    manifest = _manifest(
        targets=targets,
        arms=arms,
        replicate=replicate,
        run_prefix=run_prefix,
        bioemu_num_samples=int(args.bioemu_num_samples),
        bioemu_max_attempted_structures=int(args.bioemu_max_attempted_structures),
        bioemu_max_return_structures=int(args.bioemu_max_return_structures),
        bioemu_target_rmsd_cutoff=float(args.bioemu_target_rmsd_cutoff),
    )
    manifest_path = RESULTS_DIR / f"{run_prefix}_manifest.json"
    _write_json(manifest_path, manifest)
    print(f"env_file={env_file or 'not found'}")
    print(f"output_root={os.environ['PIPELINE_OUTPUT_ROOT']}")
    print(f"wrote manifest: {manifest_path}")

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    runner = build_runner()
    launched: list[dict[str, str]] = []
    for item in manifest["jobs"]:
        run_id = str(item["run_id"])
        run_dir = Path(args.output_root).resolve() / run_id
        if run_dir.exists() and not args.force:
            print(f"[skip] {run_id}: output exists")
            launched.append(
                {"run_id": run_id, "target": str(item["target"]), "status": "skipped_existing"}
            )
            continue
        pdb_path = Path(str(item["pdb_path"]))
        request = build_request(
            pdb_path.read_text(encoding="utf-8"),
            str(item["arm"]),
            seed=replicate,
        )
        request = replace(
            request,
            bioemu_num_samples=int(args.bioemu_num_samples),
            bioemu_max_attempted_structures=int(args.bioemu_max_attempted_structures),
            bioemu_max_return_structures=int(args.bioemu_max_return_structures),
            bioemu_target_rmsd_cutoff=float(args.bioemu_target_rmsd_cutoff),
            force=bool(args.force),
            auto_recover=True,
        )
        print(
            f"[run] {run_id}: target={item['target']} arm={item['arm']} "
            f"bioemu_samples={args.bioemu_num_samples} "
            f"max_attempted={args.bioemu_max_attempted_structures} "
            f"rmsd_cutoff={args.bioemu_target_rmsd_cutoff}"
        )
        try:
            runner.run(request, run_id=run_id)
        except Exception as exc:
            print(f"[error] {run_id}: {exc}", file=sys.stderr)
            launched.append(
                {"run_id": run_id, "target": str(item["target"]), "status": "error"}
            )
            if args.stop_on_error:
                return 1
            continue
        launched.append(
            {"run_id": run_id, "target": str(item["target"]), "status": "completed"}
        )
    print(json.dumps({"runs": launched}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--targets", default=DEFAULT_TARGETS)
    parser.add_argument("--arms", default=DEFAULT_ARMS)
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument(
        "--run-prefix",
        default=time.strftime("bioemu_sensitivity_%Y%m%d_%H%M%S", time.gmtime()),
    )
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--bioemu-num-samples", type=int, default=30)
    parser.add_argument("--bioemu-max-attempted-structures", type=int, default=30)
    parser.add_argument("--bioemu-max-return-structures", type=int, default=3)
    parser.add_argument("--bioemu-target-rmsd-cutoff", type=float, default=2.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)

    if args.run_now:
        return run_now(args)

    env_file = _load_env(args.env_file)
    os.environ["PIPELINE_OUTPUT_ROOT"] = str(Path(args.output_root).resolve())
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--run-now",
        "--targets",
        str(args.targets),
        "--arms",
        str(args.arms),
        "--replicate",
        str(args.replicate),
        "--run-prefix",
        str(args.run_prefix),
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--bioemu-num-samples",
        str(args.bioemu_num_samples),
        "--bioemu-max-attempted-structures",
        str(args.bioemu_max_attempted_structures),
        "--bioemu-max-return-structures",
        str(args.bioemu_max_return_structures),
        "--bioemu-target-rmsd-cutoff",
        str(args.bioemu_target_rmsd_cutoff),
    ]
    for flag, enabled in (
        ("--force", args.force),
        ("--dry-run", args.dry_run),
        ("--stop-on-error", args.stop_on_error),
    ):
        if enabled:
            command.append(flag)
    if args.env_file:
        command.extend(["--env-file", str(args.env_file)])

    targets = _parse_csv(args.targets)
    arms = _parse_csv(args.arms)
    metadata = {
        "env_file": str(env_file) if env_file else None,
        "targets": targets,
        "arms": arms,
        "output_root": str(Path(args.output_root).resolve()),
        "run_prefix": str(args.run_prefix),
        "bioemu_num_samples": int(args.bioemu_num_samples),
        "bioemu_max_attempted_structures": int(
            args.bioemu_max_attempted_structures
        ),
        "bioemu_max_return_structures": int(args.bioemu_max_return_structures),
        "bioemu_target_rmsd_cutoff": float(args.bioemu_target_rmsd_cutoff),
        "command": command,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **metadata}, ensure_ascii=False, indent=2))
        return 0

    job = _launch_helper(
        str(Path(args.output_root).resolve()),
        kind="paper_bioemu_sensitivity",
        label=f"Paper BioEmu RMSD-gate sensitivity ({len(targets)} targets)",
        command=command,
        metadata=metadata,
        cwd=str(PROJECT_ROOT),
    )
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
