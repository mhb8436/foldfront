#!/usr/bin/env python3
"""Launch manuscript AF2-budgeted surrogate-triage runs.

This script is the paper-facing compute-saving path. By default it runs the
original-backbone RAPID design pipeline with RFD3, BioEmu, Relax, and
experimental evolution disabled, then enables one-round surrogate triage before
AF2/ColabFold. Optional structural-context flags can be used for supplementary
compatibility runs that combine RFD3 and/or BioEmu with the same surrogate
triage budget.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pipeline_mcp.app import build_runner
from pipeline_mcp.cath_ops import _launch_helper


DEFAULT_TARGETS = (
    "cath_val_1a19A00,"
    "cath_val_1advA02,"
    "cath_val_1h6wA03,"
    "cath_train_1a6jA00,"
    "cath_train_1a8rG01"
)
_HTTP_COLABFOLD_ENV = ("COLABFOLD_URL", "COLABFOLD_HTTP_URL", "COLABFOLD_GPU_URL")


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


def _load_cath_batch_module():
    path = PROJECT_ROOT / "scripts" / "02_run_cath_batch.py"
    spec = importlib.util.spec_from_file_location("rapid_cath_batch", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_targets(text: str) -> list[str]:
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def _parse_models(text: str, *, default: str | list[str]) -> str | list[str]:
    items = [item.strip().lower() for item in re.split(r"[,;\n]+", str(text or "")) if item.strip()]
    if not items:
        return list(default) if isinstance(default, list) else default
    return items[0] if len(items) == 1 else items


def _parse_tiers(text: str) -> list[float] | None:
    items = [item.strip() for item in re.split(r"[,;\n]+", str(text or "")) if item.strip()]
    if not items:
        return None
    return [float(item) for item in items]


def _run_state(run_dir: Path) -> str:
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return "unknown"
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown"
    return str(payload.get("state") or "").strip().lower() or "unknown"


def _validate_budget_args(args: argparse.Namespace) -> None:
    selected_tiers = _parse_tiers(getattr(args, "selected_tiers", "") or "")
    tier_count = len(selected_tiers or [0.3, 0.5, 0.7])
    scope = str(getattr(args, "surrogate_scope", "pooled_tiers") or "pooled_tiers")
    planned_pool = int(args.num_seq_per_tier) * (
        tier_count if scope == "pooled_tiers" else 1
    )
    budget = int(args.initial_samples) + int(args.top_k)
    if planned_pool <= budget and not bool(args.allow_untriaged_small_pool):
        raise ValueError(
            "candidate pool size must exceed initial_samples + top_k for the paper "
            f"surrogate-triage run. Got pool={planned_pool}, "
            f"num_seq_per_tier={args.num_seq_per_tier}, tiers={tier_count}, "
            f"scope={scope}, "
            f"initial_samples={args.initial_samples}, top_k={args.top_k}. "
            "Use --allow-untriaged-small-pool only for smoke tests."
        )


def _apply_af2_backend(args: argparse.Namespace) -> None:
    backend = str(args.af2_backend or "auto").strip().lower()
    if backend == "runpod":
        for name in _HTTP_COLABFOLD_ENV:
            os.environ.pop(name, None)
    if int(args.pipeline_af2_max_workers or 0) > 0:
        os.environ["PIPELINE_AF2_MAX_WORKERS"] = str(int(args.pipeline_af2_max_workers))


def _validate_runner_backend(runner, *, requested_backend: str) -> str:
    client = getattr(runner, "colabfold", None)
    actual = type(client).__name__ if client is not None else "None"
    requested = str(requested_backend or "auto").strip().lower()
    if requested == "runpod" and actual != "AlphaFold2RunPodClient":
        raise RuntimeError(
            "af2_backend=runpod was requested, but ColabFold resolved to "
            f"{actual}. Clear COLABFOLD_URL/COLABFOLD_HTTP_URL/COLABFOLD_GPU_URL "
            "or configure COLABFOLD_ENDPOINT_ID."
        )
    if requested == "http" and actual != "LocalHTTPAlphaFold2Client":
        raise RuntimeError(
            "af2_backend=http was requested, but ColabFold did not resolve to "
            "the local HTTP client. Configure COLABFOLD_URL or COLABFOLD_HTTP_URL."
        )
    return actual


def _split_target(target: str) -> tuple[str | None, str]:
    for subset in ("train", "val", "test"):
        prefix = f"cath_{subset}_"
        if target.startswith(prefix):
            return subset, target.removeprefix(prefix)
    return None, target.removesuffix(".pdb")


def _resolve_pdb(target: str, source_root: Path) -> Path:
    path = Path(target)
    if path.exists():
        return path
    subset, target_id = _split_target(target)
    subsets = [subset] if subset else ["val", "train", "test"]
    for item in subsets:
        if not item:
            continue
        candidate = source_root / f"cath_{item}" / f"{target_id}.pdb"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"PDB not found for target {target!r} under {source_root}")


def _safe_run_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._")


def _build_request(*, cath_module, pdb_path: Path, target_id: str, args: argparse.Namespace):
    base = cath_module.build_cath_request(
        pdb_path.read_text(encoding="utf-8"),
        target_id=target_id,
        num_seq_per_tier=int(args.num_seq_per_tier),
        af2_max_candidates_per_tier=0,
        af2_top_k=0,
    )
    selected_tiers = _parse_tiers(args.selected_tiers)
    return replace(
        base,
        evolution_mode=False,
        surrogate_triage_enabled=True,
        surrogate_triage_scope=str(args.surrogate_scope),
        surrogate_triage_initial_samples=int(args.initial_samples),
        surrogate_triage_top_k=int(args.top_k),
        surrogate_triage_model=_parse_models(args.surrogate_policy, default="auto"),
        surrogate_triage_comparator_models=_parse_models(
            args.comparator_models,
            default=["rf", "ridge", "lightgbm", "xgboost"],
        ),
        surrogate_triage_ensemble_models=_parse_models(args.ensemble_models, default=[]),
        surrogate_triage_cv_folds=int(args.cv_folds),
        rfd3_use=bool(args.rfd3_use),
        rfd3_max_return_designs=max(1, int(args.rfd3_max_return_designs)),
        rfd3_target_rmsd_cutoff=float(args.rfd3_target_rmsd_cutoff),
        bioemu_use=bool(args.bioemu_use),
        bioemu_num_samples=max(1, int(args.bioemu_num_samples)),
        bioemu_max_return_structures=max(1, int(args.bioemu_max_return_structures)),
        bioemu_filter_samples=bool(args.bioemu_filter_samples),
        bioemu_target_rmsd_cutoff=float(args.bioemu_target_rmsd_cutoff),
        relax_enabled=bool(args.relax_enabled),
        novelty_enabled=(str(args.stop_after).lower() == "novelty"),
        wt_compare=False,
        agent_panel_enabled=False,
        soluprot_cutoff=float(args.soluprot_cutoff),
        af2_provider=str(args.af2_provider),
        af2_plddt_cutoff=float(args.af2_plddt_cutoff),
        af2_rmsd_cutoff=float(args.af2_rmsd_cutoff),
        selected_tiers=selected_tiers,
        stop_after=str(args.stop_after),
        force=bool(args.force),
        auto_recover=False,
    )


def run_now(args: argparse.Namespace) -> int:
    _validate_budget_args(args)
    env_file = _load_env(args.env_file)
    os.environ["PIPELINE_OUTPUT_ROOT"] = str(Path(args.output_root).resolve())
    _apply_af2_backend(args)
    print(f"env_file={env_file or 'not found'}")
    print(f"output_root={os.environ['PIPELINE_OUTPUT_ROOT']}")
    print(f"af2_backend={args.af2_backend}")
    print(f"PIPELINE_AF2_MAX_WORKERS={os.environ.get('PIPELINE_AF2_MAX_WORKERS', '')}")

    source_root = Path(args.source_root).resolve()
    targets = _parse_targets(args.targets)
    cath_module = _load_cath_batch_module()
    runner = None if args.dry_run else build_runner()
    if runner is not None:
        actual_backend = _validate_runner_backend(
            runner, requested_backend=str(args.af2_backend)
        )
        print(f"resolved_colabfold_client={actual_backend}")
    run_prefix = str(args.run_prefix or "").strip() or time.strftime(
        "paper_surrogate_%Y%m%d", time.gmtime()
    )
    launched: list[dict[str, str]] = []

    for target in targets:
        subset, target_id = _split_target(target)
        pdb_path = _resolve_pdb(target, source_root)
        run_id = _safe_run_id(f"{run_prefix}_{target}")
        run_dir = Path(args.output_root).resolve() / run_id
        if run_dir.exists() and not args.force:
            state = _run_state(run_dir)
            if state == "completed":
                print(f"[skip] {run_id}: completed output exists")
                launched.append(
                    {"target": target, "run_id": run_id, "status": "skipped_completed"}
                )
                continue
            if not args.resume_existing:
                raise RuntimeError(
                    f"output exists but is not completed for {run_id} "
                    f"(state={state}). Use a new --run-prefix, --resume-existing, "
                    "or --force."
                )
            print(f"[resume] {run_id}: existing output state={state}")
        request = _build_request(
            cath_module=cath_module,
            pdb_path=pdb_path,
            target_id=target_id,
            args=args,
        )
        print(
            f"[run] {run_id}: target={target} split={subset or '-'} "
            f"n_train={args.initial_samples} top_k={args.top_k} "
            f"scope={args.surrogate_scope} policy={args.surrogate_policy} "
            f"comparators={args.comparator_models} "
            f"rfd3={int(bool(args.rfd3_use))} bioemu={int(bool(args.bioemu_use))}"
        )
        if args.dry_run:
            launched.append({"target": target, "run_id": run_id, "status": "dry_run"})
            continue
        if runner is None:
            raise RuntimeError("runner was not initialized")
        runner.run(request, run_id=run_id)
        launched.append({"target": target, "run_id": run_id, "status": "submitted"})

    print(json.dumps({"runs": launched}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--targets", default=DEFAULT_TARGETS)
    parser.add_argument("--source-root", default="/opt/protein_pipeline")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--run-prefix", default=time.strftime("paper_surrogate_%Y%m%d", time.gmtime()))
    parser.add_argument("--initial-samples", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--num-seq-per-tier", type=int, default=3333)
    parser.add_argument(
        "--surrogate-scope",
        choices=["pooled_tiers", "per_tier"],
        default="pooled_tiers",
        help=(
            "pooled_tiers applies one AF2 training+Top-K budget across all selected "
            "conservation tiers; per_tier repeats the budget inside each tier."
        ),
    )
    parser.add_argument(
        "--surrogate-policy",
        "--surrogate-models",
        dest="surrogate_policy",
        default="auto",
        help=(
            "Top-K selection method for final candidates. Use auto to select by "
            "internal CV; rf/ridge/xgboost/lightgbm/ensemble force one policy. "
            "--surrogate-models is kept as a backward-compatible alias."
        ),
    )
    parser.add_argument("--comparator-models", default="rf,ridge,lightgbm,xgboost")
    parser.add_argument("--ensemble-models", default="")
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--soluprot-cutoff", type=float, default=0.5)
    parser.add_argument("--af2-provider", default="colabfold")
    parser.add_argument(
        "--af2-backend",
        choices=["runpod", "http", "auto"],
        default="runpod",
        help=(
            "Provider backend for ColabFold. The paper run defaults to RunPod so "
            "job ids are recorded and long HTTP /run requests cannot hide progress."
        ),
    )
    parser.add_argument(
        "--pipeline-af2-max-workers",
        type=int,
        default=8,
        help="Temporary PIPELINE_AF2_MAX_WORKERS value for this managed paper run.",
    )
    parser.add_argument("--af2-plddt-cutoff", type=float, default=85.0)
    parser.add_argument("--af2-rmsd-cutoff", type=float, default=2.0)
    parser.add_argument(
        "--rfd3-use",
        action="store_true",
        help="Enable RFD3 before ProteinMPNN for structural-context surrogate compatibility runs.",
    )
    parser.add_argument("--rfd3-max-return-designs", type=int, default=10)
    parser.add_argument("--rfd3-target-rmsd-cutoff", type=float, default=2.0)
    parser.add_argument(
        "--bioemu-use",
        action="store_true",
        help="Enable BioEmu before ProteinMPNN for structural-context surrogate compatibility runs.",
    )
    parser.add_argument("--bioemu-num-samples", type=int, default=50)
    parser.add_argument("--bioemu-max-return-structures", type=int, default=10)
    parser.add_argument("--bioemu-target-rmsd-cutoff", type=float, default=2.0)
    parser.add_argument("--bioemu-filter-samples", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--relax-enabled", action="store_true")
    parser.add_argument(
        "--selected-tiers",
        default="",
        help="Optional comma-separated conservation tiers for pilot runs, e.g. 0.3.",
    )
    parser.add_argument("--stop-after", choices=["af2", "novelty"], default="af2")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--allow-untriaged-small-pool", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        _validate_budget_args(args)
    except ValueError as exc:
        parser.error(str(exc))

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
        "--source-root",
        str(args.source_root),
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--run-prefix",
        str(args.run_prefix),
        "--initial-samples",
        str(args.initial_samples),
        "--top-k",
        str(args.top_k),
        "--num-seq-per-tier",
        str(args.num_seq_per_tier),
        "--surrogate-scope",
        str(args.surrogate_scope),
        "--surrogate-policy",
        str(args.surrogate_policy),
        "--comparator-models",
        str(args.comparator_models),
        "--cv-folds",
        str(args.cv_folds),
        "--soluprot-cutoff",
        str(args.soluprot_cutoff),
        "--af2-provider",
        str(args.af2_provider),
        "--af2-backend",
        str(args.af2_backend),
        "--pipeline-af2-max-workers",
        str(args.pipeline_af2_max_workers),
        "--af2-plddt-cutoff",
        str(args.af2_plddt_cutoff),
        "--af2-rmsd-cutoff",
        str(args.af2_rmsd_cutoff),
        "--stop-after",
        str(args.stop_after),
    ]
    if args.rfd3_use:
        command.append("--rfd3-use")
    command.extend([
        "--rfd3-max-return-designs",
        str(args.rfd3_max_return_designs),
        "--rfd3-target-rmsd-cutoff",
        str(args.rfd3_target_rmsd_cutoff),
    ])
    if args.bioemu_use:
        command.append("--bioemu-use")
    command.extend([
        "--bioemu-num-samples",
        str(args.bioemu_num_samples),
        "--bioemu-max-return-structures",
        str(args.bioemu_max_return_structures),
        "--bioemu-target-rmsd-cutoff",
        str(args.bioemu_target_rmsd_cutoff),
    ])
    command.append("--bioemu-filter-samples" if args.bioemu_filter_samples else "--no-bioemu-filter-samples")
    if args.relax_enabled:
        command.append("--relax-enabled")
    if str(args.ensemble_models or "").strip():
        command.extend(["--ensemble-models", str(args.ensemble_models)])
    if str(args.selected_tiers or "").strip():
        command.extend(["--selected-tiers", str(args.selected_tiers)])
    for flag, enabled in (
        ("--resume-existing", args.resume_existing),
        ("--allow-untriaged-small-pool", args.allow_untriaged_small_pool),
        ("--force", args.force),
        ("--dry-run", args.dry_run),
    ):
        if enabled:
            command.append(flag)
    if args.env_file:
        command.extend(["--env-file", str(args.env_file)])

    metadata = {
        "env_file": str(env_file) if env_file else None,
        "targets": _parse_targets(args.targets),
        "source_root": str(args.source_root),
        "output_root": str(Path(args.output_root).resolve()),
        "initial_samples": int(args.initial_samples),
        "top_k": int(args.top_k),
        "num_seq_per_tier": int(args.num_seq_per_tier),
        "surrogate_scope": str(args.surrogate_scope),
        "surrogate_policy": str(args.surrogate_policy),
        "comparator_models": str(args.comparator_models),
        "ensemble_models": str(args.ensemble_models),
        "cv_folds": int(args.cv_folds),
        "soluprot_cutoff": float(args.soluprot_cutoff),
        "af2_provider": str(args.af2_provider),
        "af2_backend": str(args.af2_backend),
        "pipeline_af2_max_workers": int(args.pipeline_af2_max_workers),
        "rfd3_use": bool(args.rfd3_use),
        "rfd3_max_return_designs": int(args.rfd3_max_return_designs),
        "rfd3_target_rmsd_cutoff": float(args.rfd3_target_rmsd_cutoff),
        "bioemu_use": bool(args.bioemu_use),
        "bioemu_num_samples": int(args.bioemu_num_samples),
        "bioemu_max_return_structures": int(args.bioemu_max_return_structures),
        "bioemu_filter_samples": bool(args.bioemu_filter_samples),
        "bioemu_target_rmsd_cutoff": float(args.bioemu_target_rmsd_cutoff),
        "relax_enabled": bool(args.relax_enabled),
        "selected_tiers": str(args.selected_tiers),
        "stop_after": str(args.stop_after),
        "command": command,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **metadata}, ensure_ascii=False, indent=2))
        return 0

    job = _launch_helper(
        str(Path(args.output_root).resolve()),
        kind="paper_surrogate_triage_budget",
        label=f"Paper surrogate-triage budget ({len(metadata['targets'])} targets)",
        command=command,
        metadata=metadata,
        cwd=str(PROJECT_ROOT),
    )
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
