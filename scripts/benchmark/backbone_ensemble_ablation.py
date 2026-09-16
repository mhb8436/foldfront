#!/usr/bin/env python3
"""Run and summarize the backbone/ensemble ablation used in the manuscript."""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pipeline_mcp.models import PipelineRequest


DEFAULT_TARGETS = ["1kvdD00", "3bukC01", "2wejA00"]
DEFAULT_ARMS = ["single", "bioemu", "rfd3_single", "rfd3_bioemu"]
TIERS = [0.3, 0.5, 0.7]
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark" / "results"
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"

SEQUENCE_FIELDS = [
    "target",
    "arm",
    "arm_label",
    "replicate",
    "run_id",
    "tier",
    "seq_id",
    "sequence",
    "backbone_id",
    "backbone_source",
    "plddt",
    "soluprot",
]
SUMMARY_FIELDS = [
    "target",
    "arm",
    "arm_label",
    "replicate",
    "n_designs",
    "n_plddt",
    "n_soluprot",
    "n_backbones_observed",
    "backbone_sources",
    "mean_plddt",
    "std_plddt",
    "range_plddt",
    "max_plddt",
    "top5_mean_plddt",
    "plddt_pass_rate_85",
    "mean_soluprot",
    "std_soluprot",
    "range_soluprot",
    "max_soluprot",
    "top5_mean_soluprot",
    "soluprot_pass_rate_0_5",
    "mean_pairwise_identity",
    "mean_pairwise_diversity",
]
PAIRED_FIELDS = [
    "comparison",
    "metric",
    "n_pairs",
    "mean_delta",
    "median_delta",
    "wilcoxon_p",
]


ARM_CONFIGS: dict[str, dict[str, Any]] = {
    "single": {
        "label": "Single target backbone",
        "rfd3_use": False,
        "rfd3_use_ensemble": False,
        "rfd3_max_return_designs": 1,
        "bioemu_use": False,
        "bioemu_num_samples": 0,
        "bioemu_max_return_structures": 0,
        "num_seq_per_tier": 40,
    },
    "bioemu": {
        "label": "Target + BioEmu ensemble",
        "rfd3_use": False,
        "rfd3_use_ensemble": False,
        "rfd3_max_return_designs": 1,
        "bioemu_use": True,
        "bioemu_num_samples": 10,
        "bioemu_max_return_structures": 3,
        # target + 3 BioEmu structures x 3 tiers x 10 designs = 120 designs.
        "num_seq_per_tier": 10,
    },
    "rfd3_single": {
        "label": "RFD3 selected backbone",
        "rfd3_use": True,
        "rfd3_use_ensemble": False,
        "rfd3_max_return_designs": 1,
        "bioemu_use": False,
        "bioemu_num_samples": 0,
        "bioemu_max_return_structures": 0,
        "num_seq_per_tier": 40,
    },
    "rfd3_bioemu": {
        "label": "RFD3 + BioEmu ensemble",
        "rfd3_use": True,
        "rfd3_use_ensemble": False,
        "rfd3_max_return_designs": 1,
        "bioemu_use": True,
        "bioemu_num_samples": 10,
        "bioemu_max_return_structures": 3,
        # 1 RFD3 + 3 BioEmu structures x 3 tiers x 10 designs = 120 designs.
        "num_seq_per_tier": 10,
    },
    "rfd3_ensemble3": {
        "label": "RFD3 ensemble, 3 backbones",
        "rfd3_use": True,
        "rfd3_use_ensemble": True,
        "rfd3_max_return_designs": 3,
        "bioemu_use": False,
        "bioemu_num_samples": 0,
        "bioemu_max_return_structures": 0,
        # 3 backbones x 3 tiers x 13 designs = 117 designs, close to the
        # 120-design budget used by the single-backbone arms.
        "num_seq_per_tier": 13,
    },
}


def _ordered_arms(observed: set[str]) -> list[str]:
    order = list(DEFAULT_ARMS) + [
        arm for arm in ARM_CONFIGS if arm not in set(DEFAULT_ARMS)
    ]
    return [arm for arm in order if arm in observed]


def _planned_backbone_count(cfg: dict[str, Any]) -> int:
    count = 0
    if bool(cfg.get("rfd3_use")):
        count += max(1, int(cfg.get("rfd3_max_return_designs") or 1))
    else:
        # In the pipeline, the input target is the backbone unless RFD3 supplies one.
        count += 1
    if bool(cfg.get("bioemu_use")):
        count += max(1, int(cfg.get("bioemu_max_return_structures") or 1))
    return max(1, count)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_fasta(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    header: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        nonlocal header, chunks
        if header is None:
            return
        pieces = header.split("|")
        seq_id = pieces[0].split()[0]
        meta: dict[str, str] = {}
        for piece in pieces[1:]:
            if "=" in piece:
                key, value = piece.split("=", 1)
                meta[key] = value
        records.append(
            {
                "seq_id": seq_id,
                "header": header,
                "sequence": "".join(chunks),
                "meta": meta,
            }
        )
        header = None
        chunks = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            header = line[1:].strip()
        else:
            chunks.append(line)
    flush()
    return records


def build_request(pdb_text: str, arm: str, *, seed: int) -> PipelineRequest:
    if arm not in ARM_CONFIGS:
        raise ValueError(f"unknown arm: {arm}")
    cfg = ARM_CONFIGS[arm]
    bioemu_num_samples = int(cfg.get("bioemu_num_samples") or 0)
    bioemu_max_return_structures = int(cfg.get("bioemu_max_return_structures") or 0)
    return PipelineRequest(
        target_fasta="",
        target_pdb=pdb_text,
        rfd3_use=bool(cfg["rfd3_use"]),
        rfd3_use_ensemble=bool(cfg["rfd3_use_ensemble"]),
        rfd3_max_return_designs=int(cfg["rfd3_max_return_designs"]),
        rfd3_partial_t=5.0,
        rfd3_target_rmsd_cutoff=2.0,
        bioemu_use=bool(cfg.get("bioemu_use", False)),
        bioemu_num_samples=bioemu_num_samples,
        bioemu_max_return_structures=bioemu_max_return_structures,
        bioemu_base_seed=int(seed),
        bioemu_batch_size_100=1,
        bioemu_max_attempted_structures=max(
            bioemu_num_samples,
            bioemu_max_return_structures,
        ),
        conservation_tiers=list(TIERS),
        ligand_mask_distance=6.0,
        ligand_mask_use_original_target=True,
        pdb_strip_nonpositive_resseq=True,
        pdb_renumber_resseq_from_1=True,
        num_seq_per_tier=int(cfg["num_seq_per_tier"]),
        sampling_temp=0.1,
        seed=int(seed),
        soluprot_cutoff=0.0,
        af2_provider="colabfold",
        af2_max_candidates_per_tier=10,
        af2_top_k=0,
        relax_enabled=False,
        novelty_enabled=False,
        wt_compare=False,
        agent_panel_enabled=False,
        stop_after="af2",
        force=False,
        auto_recover=True,
    )


def build_run_id(target: str, arm: str, replicate: int) -> str:
    clean_target = target.replace(".", "_").replace("/", "_")
    clean_arm = arm.replace(".", "_").replace("/", "_")
    return f"abl_be_{clean_target}_{clean_arm}_s{int(replicate)}"


def existing_run_action(run_dir: Path, *, force: bool, resume_existing: bool) -> str:
    if force or not run_dir.exists():
        return "run"
    status = _read_json(run_dir / "status.json")
    stage = str(status.get("stage") or "").strip().lower()
    state = str(status.get("state") or "").strip().lower()
    if stage == "done" and state == "completed":
        return "skip_completed"
    return "resume" if resume_existing else "skip_existing"


def resume_start_from(status: dict[str, Any]) -> str:
    stage = str(status.get("stage") or "").strip().lower()
    if stage in {"", "init", "mmseqs_msa"}:
        return "msa"
    if stage == "rfd3" or stage.startswith("rfd3_"):
        return "rfd3"
    if stage == "bioemu" or stage.startswith("bioemu_"):
        return "bioemu"
    if stage.startswith("relax_"):
        return "relax"
    if stage.startswith("novelty_"):
        return "novelty"
    return "design"


def load_resume_request(run_dir: Path) -> PipelineRequest:
    data = _read_json(run_dir / "request.json")
    if not data:
        raise ValueError(f"cannot resume without request.json: {run_dir}")
    status = _read_json(run_dir / "status.json")
    request = PipelineRequest(**data)
    return replace(
        request,
        start_from=resume_start_from(status),
        force=False,
        auto_recover=True,
    )


def _target_to_pdb_path(target: str) -> Path:
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("target is required")
    path = Path(raw)
    if path.exists():
        return path

    subset_hint: str | None = None
    name = raw
    for subset in ("train", "val", "test"):
        prefix = f"cath_{subset}_"
        if name.startswith(prefix):
            subset_hint = subset
            name = name.replace(prefix, "", 1)
            break
    if name.endswith(".pdb"):
        name = Path(name).stem

    manifest_paths = [
        PROJECT_ROOT / "data" / "benchmark" / "results" / "rapid_target_manifest.csv",
        PROJECT_ROOT
        / "public_data"
        / "benchmark"
        / "results"
        / "rapid_target_manifest.csv",
    ]
    for manifest_path in manifest_paths:
        if not manifest_path.exists():
            continue
        try:
            with manifest_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("target") or "").strip() != name:
                        continue
                    split = str(row.get("split") or "").strip().lower()
                    if subset_hint and split and split != subset_hint:
                        continue
                    local_candidate = PROJECT_ROOT / f"cath_{split}" / f"{name}.pdb"
                    if split in {"train", "val", "test"} and local_candidate.exists():
                        return local_candidate
                    recorded = Path(str(row.get("pdb_path") or "").strip())
                    if recorded.exists():
                        return recorded
        except Exception:
            continue

    subsets = [subset_hint] if subset_hint else ["test", "val", "train"]
    for subset in subsets:
        if not subset:
            continue
        candidate = PROJECT_ROOT / f"cath_{subset}" / f"{name}.pdb"
        if candidate.exists():
            return candidate
    fallback_subset = subset_hint or "test"
    return PROJECT_ROOT / f"cath_{fallback_subset}" / f"{name}.pdb"


def _scores(path: Path) -> dict[str, float]:
    data = _read_json(path)
    raw = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except Exception:
            continue
    return out


def collect_run_rows(
    run_dir: Path,
    *,
    target: str,
    arm: str,
    replicate: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tiers_dir = run_dir / "tiers"
    if not tiers_dir.is_dir():
        return rows
    for tier_dir in sorted(tiers_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        try:
            tier = int(tier_dir.name)
        except ValueError:
            continue
        fasta = tier_dir / "designs_filtered.fasta"
        if not fasta.exists():
            fasta = tier_dir / "designs.fasta"
        if not fasta.exists():
            continue
        plddt_scores = _scores(tier_dir / "af2_scores.json")
        soluprot_scores = _scores(tier_dir / "soluprot.json")
        for record in parse_fasta(fasta.read_text(encoding="utf-8")):
            seq_id = str(record["seq_id"])
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            rows.append(
                {
                    "target": target,
                    "arm": arm,
                    "arm_label": ARM_CONFIGS.get(arm, {}).get("label", arm),
                    "replicate": int(replicate),
                    "run_id": run_dir.name,
                    "tier": tier,
                    "seq_id": seq_id,
                    "sequence": str(record.get("sequence") or ""),
                    "backbone_id": str(meta.get("backbone") or ""),
                    "backbone_source": str(meta.get("source") or ""),
                    "plddt": plddt_scores.get(seq_id),
                    "soluprot": soluprot_scores.get(seq_id),
                }
            )
    return rows


def _mean(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return float(sum(clean) / len(clean)) if clean else None


def _max(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return float(max(clean)) if clean else None


def _std(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return float(statistics.pstdev(clean)) if len(clean) >= 2 else None


def _range(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return float(max(clean) - min(clean)) if len(clean) >= 2 else None


def _top_mean(values: list[float], top_k: int) -> float | None:
    clean = sorted(
        [float(v) for v in values if v is not None and not math.isnan(float(v))],
        reverse=True,
    )
    if not clean:
        return None
    subset = clean[: max(1, int(top_k))]
    return float(sum(subset) / len(subset))


def pairwise_identity(sequences: list[str]) -> float | None:
    clean = [seq for seq in sequences if seq]
    if len(clean) < 2:
        return None
    values: list[float] = []
    for i, left in enumerate(clean):
        for right in clean[i + 1 :]:
            denom = max(len(left), len(right), 1)
            matches = sum(1 for a, b in zip(left, right) if a == b)
            values.append(matches / denom)
    return float(sum(values) / len(values)) if values else None


def summarize_group(rows: list[dict[str, Any]], *, top_k: int = 5) -> dict[str, Any]:
    plddt = [r["plddt"] for r in rows if r.get("plddt") is not None]
    soluprot = [r["soluprot"] for r in rows if r.get("soluprot") is not None]
    sources = sorted({str(r.get("backbone_source") or "") for r in rows if r.get("backbone_source")})
    backbone_ids = sorted({str(r.get("backbone_id") or "") for r in rows if r.get("backbone_id")})
    top_label = f"top{int(top_k)}"
    mean_identity = pairwise_identity(
        [str(r.get("sequence") or "") for r in rows]
    )
    return {
        "n_designs": len(rows),
        "n_plddt": len(plddt),
        "n_soluprot": len(soluprot),
        "n_backbones_observed": len(backbone_ids),
        "backbone_sources": ";".join(sources),
        "mean_plddt": _mean(plddt),
        "std_plddt": _std(plddt),
        "range_plddt": _range(plddt),
        "max_plddt": _max(plddt),
        f"{top_label}_mean_plddt": _top_mean(plddt, top_k),
        "plddt_pass_rate_85": (
            float(sum(1 for v in plddt if float(v) >= 85.0) / len(plddt))
            if plddt
            else None
        ),
        "mean_soluprot": _mean(soluprot),
        "std_soluprot": _std(soluprot),
        "range_soluprot": _range(soluprot),
        "max_soluprot": _max(soluprot),
        f"{top_label}_mean_soluprot": _top_mean(soluprot, top_k),
        "soluprot_pass_rate_0_5": (
            float(sum(1 for v in soluprot if float(v) >= 0.5) / len(soluprot))
            if soluprot
            else None
        ),
        "mean_pairwise_identity": mean_identity,
        "mean_pairwise_diversity": (
            float(1.0 - mean_identity) if mean_identity is not None else None
        ),
    }


def collect_all_rows(
    *,
    output_root: Path,
    targets: list[str],
    arms: list[str],
    replicates: list[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        for arm in arms:
            for replicate in replicates:
                run_id = build_run_id(target, arm, replicate)
                rows.extend(
                    collect_run_rows(
                        output_root / run_id,
                        target=target,
                        arm=arm,
                        replicate=replicate,
                    )
                )
    return rows


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fieldnames or [])
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_summary(rows: list[dict[str, Any]], *, top_k: int = 5) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["target"]), str(row["arm"]), int(row["replicate"]))
        grouped.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for (target, arm, replicate), group_rows in sorted(grouped.items()):
        summary = summarize_group(group_rows, top_k=top_k)
        out.append(
            {
                "target": target,
                "arm": arm,
                "arm_label": ARM_CONFIGS.get(arm, {}).get("label", arm),
                "replicate": replicate,
                **summary,
            }
        )
    return out


def _paired_tests(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not summary_rows:
        return []
    try:
        from scipy.stats import wilcoxon
    except Exception:
        wilcoxon = None
    metrics = [
        "mean_plddt",
        "std_plddt",
        "range_plddt",
        "max_plddt",
        "top5_mean_plddt",
        "plddt_pass_rate_85",
        "mean_soluprot",
        "std_soluprot",
        "range_soluprot",
        "max_soluprot",
        "top5_mean_soluprot",
        "soluprot_pass_rate_0_5",
        "mean_pairwise_identity",
        "mean_pairwise_diversity",
    ]
    by_key = {
        (str(row["target"]), int(row["replicate"]), str(row["arm"])): row
        for row in summary_rows
    }
    targets = sorted({str(row["target"]) for row in summary_rows})
    reps = sorted({int(row["replicate"]) for row in summary_rows})
    tests: list[dict[str, Any]] = []
    observed_arms = {str(row["arm"]) for row in summary_rows}
    for arm in [a for a in _ordered_arms(observed_arms) if a != "single"]:
        for metric in metrics:
            diffs: list[float] = []
            for target in targets:
                for rep in reps:
                    base = by_key.get((target, rep, "single"))
                    comp = by_key.get((target, rep, arm))
                    if not base or not comp:
                        continue
                    left = base.get(metric)
                    right = comp.get(metric)
                    if left in (None, "") or right in (None, ""):
                        continue
                    diffs.append(float(right) - float(left))
            p_value = None
            if wilcoxon is not None and len(diffs) >= 2 and any(abs(d) > 0 for d in diffs):
                try:
                    p_value = float(wilcoxon(diffs).pvalue)
                except Exception:
                    p_value = None
            tests.append(
                {
                    "comparison": f"{arm}-minus-single",
                    "metric": metric,
                    "n_pairs": len(diffs),
                    "mean_delta": _mean(diffs),
                    "median_delta": float(statistics.median(diffs)) if diffs else None,
                    "wilcoxon_p": p_value,
                }
            )
    return tests


def _write_latex_table(summary_rows: list[dict[str, Any]], out_path: Path) -> None:
    arm_order = _ordered_arms({str(r["arm"]) for r in summary_rows})
    lines = [
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "Arm & Targets & pLDDT range & SoluProt range & Pairwise diversity \\\\",
        "\\midrule",
    ]
    for arm in arm_order:
        rows = [r for r in summary_rows if r["arm"] == arm]
        if not rows:
            continue
        label = ARM_CONFIGS.get(arm, {}).get("label", arm)
        n_targets = len({str(r["target"]) for r in rows})
        plddt = _mean([float(r["range_plddt"]) for r in rows if r.get("range_plddt") not in (None, "")])
        solu = _mean([float(r["range_soluprot"]) for r in rows if r.get("range_soluprot") not in (None, "")])
        diversity = _mean([float(r["mean_pairwise_diversity"]) for r in rows if r.get("mean_pairwise_diversity") not in (None, "")])
        lines.append(
            f"{label} & {n_targets} & {plddt:.2f} & {solu:.3f} & {diversity:.3f} \\\\"
        )
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _make_figure(summary_rows: list[dict[str, Any]], out_path: Path) -> None:
    if not summary_rows:
        return
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from matplotlib.patches import Patch

    df = pd.DataFrame(summary_rows)
    arm_order = _ordered_arms(set(df["arm"]))
    labels = {
        "single": "Single",
        "bioemu": "BioEmu",
        "rfd3_single": "RFD3",
        "rfd3_bioemu": "RFD3+\nBioEmu",
        "rfd3_ensemble3": "RFD3\nensemble",
    }
    xlabels = [labels.get(arm, ARM_CONFIGS.get(arm, {}).get("label", arm)) for arm in arm_order]
    base = df[df["arm"] == "single"].copy()
    base_by_key = {
        (str(row["target"]), int(row["replicate"])): row
        for _, row in base.iterrows()
    }

    metric_specs = [
        ("range_plddt", "pLDDT candidate-pool spread", "pLDDT range"),
        ("range_soluprot", "SoluProt candidate-pool spread", "SoluProt range"),
        (
            "mean_pairwise_diversity",
            "Sequence-pool diversity",
            "Mean pairwise diversity\n(higher = lower identity)",
        ),
    ]
    absolute_by_metric: dict[str, dict[str, list[float]]] = {
        metric: {arm: [] for arm in arm_order} for metric, _, _ in metric_specs
    }
    for arm in arm_order:
        comp = df[df["arm"] == arm]
        for metric, _, _ in metric_specs:
            values: list[float] = []
            if metric not in comp:
                absolute_by_metric[metric][arm] = values
                continue
            for value in comp[metric].tolist():
                if value in (None, ""):
                    continue
                try:
                    value_f = float(value)
                except Exception:
                    continue
                if np.isnan(value_f):
                    continue
                values.append(value_f)
            absolute_by_metric[metric][arm] = values

    comparison_arms = [arm for arm in arm_order if arm != "single"]
    delta_by_metric: dict[str, dict[str, list[float]]] = {
        metric: {arm: [] for arm in comparison_arms} for metric, _, _ in metric_specs
    }
    paired_counts: dict[str, dict[str, tuple[int, int]]] = {
        metric: {arm: (0, 0) for arm in comparison_arms} for metric, _, _ in metric_specs
    }
    for arm in comparison_arms:
        comp = df[df["arm"] == arm]
        for metric, _, _ in metric_specs:
            values: list[float] = []
            for _, row in comp.iterrows():
                key = (str(row["target"]), int(row["replicate"]))
                base_row = base_by_key.get(key)
                if base_row is None:
                    continue
                left = base_row.get(metric)
                right = row.get(metric)
                if left in (None, "") or right in (None, ""):
                    continue
                try:
                    delta = float(right) - float(left)
                except Exception:
                    continue
                if np.isnan(delta):
                    continue
                values.append(delta)
            delta_by_metric[metric][arm] = values
            paired_counts[metric][arm] = (sum(1 for value in values if value > 0), len(values))

    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.7))
    axes_flat = axes.flatten()
    palette = {
        "single": "#BDBDBD",
        "bioemu": "#009E73",
        "rfd3_single": "#E69F00",
        "rfd3_bioemu": "#0072B2",
        "rfd3_ensemble3": "#CC79A7",
    }
    legend_handles = [
        Patch(
            facecolor=palette.get(arm, "#999999"),
            edgecolor="#333333",
            alpha=0.78,
            label=labels.get(arm, arm).replace("\n", " "),
        )
        for arm in arm_order
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.925),
        ncol=min(len(legend_handles), 4),
        fontsize=8,
        frameon=False,
    )
    for panel_idx, (ax, (metric, title, ylabel)) in enumerate(
        zip(axes_flat[:3], metric_specs, strict=True)
    ):
        values_by_arm = [absolute_by_metric[metric][arm] for arm in arm_order]
        nonempty_positions = [i for i, vals in enumerate(values_by_arm) if vals]
        if not nonempty_positions:
            ax.text(0.5, 0.5, "No evaluable data", ha="center", va="center")
            continue

        plot_values = [values_by_arm[i] for i in nonempty_positions]
        plot_positions = nonempty_positions
        bp = ax.boxplot(
            plot_values,
            positions=plot_positions,
            widths=0.55,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": "#333333", "linewidth": 1.2},
            whiskerprops={"color": "#555555", "linewidth": 1.0},
            capprops={"color": "#555555", "linewidth": 1.0},
            boxprops={"edgecolor": "#444444", "linewidth": 1.0},
        )
        for patch, pos in zip(bp["boxes"], plot_positions, strict=True):
            arm = arm_order[pos]
            patch.set_facecolor(palette.get(arm, "#999999"))
            patch.set_alpha(0.65)

        for i, vals in enumerate(values_by_arm):
            if not vals:
                continue
            offsets = np.linspace(-0.12, 0.12, len(vals)) if len(vals) > 1 else [0.0]
            ax.scatter(
                [i + float(offset) for offset in offsets],
                vals,
                color="#222222",
                s=18,
                alpha=0.85,
                linewidth=0,
                zorder=3,
            )
            y_values = [value for vals_ in values_by_arm for value in vals_]
            span = max(max(y_values) - min(y_values), 1e-6) if y_values else 1.0
            y_top = max(vals) + 0.08 * span
            ax.text(i, y_top, f"n={len(vals)}", ha="center", va="bottom", fontsize=7)

        all_values = [value for vals in values_by_arm for value in vals]
        if all_values:
            y_min = min(all_values)
            y_max = max(all_values)
            span = max(y_max - y_min, 1e-6)
            ax.set_ylim(y_min - 0.15 * span, y_max + 0.28 * span)
        ax.set_title(title, fontsize=10, pad=8)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_xticks(list(range(len(arm_order))))
        ax.set_xticklabels(xlabels, fontsize=8)
        ax.grid(axis="y", color="#dddddd", linewidth=0.8, alpha=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    ax = axes_flat[3]
    support_metrics = [
        ("range_plddt", "pLDDT\nspread"),
        ("range_soluprot", "SoluProt\nspread"),
        ("mean_pairwise_diversity", "Sequence\ndiversity"),
    ]
    x = np.arange(len(support_metrics))
    width = 0.22 if len(comparison_arms) > 2 else 0.28
    offsets = np.linspace(
        -width * (len(comparison_arms) - 1) / 2,
        width * (len(comparison_arms) - 1) / 2,
        len(comparison_arms),
    )
    for offset, arm in zip(offsets, comparison_arms, strict=True):
        heights = []
        labels_on_bars = []
        for metric, _ in support_metrics:
            positive, total = paired_counts[metric][arm]
            heights.append((positive / total) if total else 0.0)
            labels_on_bars.append(f"{positive}/{total}" if total else "0/0")
        bars = ax.bar(
            x + offset,
            heights,
            width=width,
            color=palette.get(arm, "#999999"),
            alpha=0.78,
            label=labels.get(arm, arm),
            edgecolor="#333333",
            linewidth=0.4,
        )
        for bar, label in zip(bars, labels_on_bars, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(bar.get_height() + 0.035, 1.05),
                label,
                ha="center",
                va="bottom",
                fontsize=7,
            )
    ax.set_title("Positive paired shifts vs Single", fontsize=10, pad=8)
    ax.set_ylabel("Fraction of evaluable\npaired targets", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([label for _, label in support_metrics], fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    for label, ax_ in zip(["A", "B", "C", "D"], axes_flat, strict=True):
        ax_.text(
            -0.16,
            1.08,
            label,
            transform=ax_.transAxes,
            fontsize=12,
            fontweight="bold",
            va="top",
        )
    fig.suptitle(
        "Structural-context ablation: context-dependent candidate-pool shifts",
        fontsize=11,
        weight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1.0, 0.89))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def _parse_csv_list(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_int_list(value: str | None, default: list[int]) -> list[int]:
    if not value:
        return list(default)
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _run_manifest(targets: list[str], arms: list[str], replicates: list[int]) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for target in targets:
        for arm in arms:
            for replicate in replicates:
                cfg = ARM_CONFIGS[arm]
                planned_backbones = _planned_backbone_count(cfg)
                planned_designs = (
                    planned_backbones
                    * len(TIERS)
                    * int(cfg["num_seq_per_tier"])
                )
                jobs.append(
                    {
                        "target": target,
                        "arm": arm,
                        "arm_label": cfg["label"],
                        "replicate": replicate,
                        "run_id": build_run_id(target, arm, replicate),
                        "pdb_path": str(_target_to_pdb_path(target)),
                        "num_seq_per_tier": cfg["num_seq_per_tier"],
                        "planned_backbones": planned_backbones,
                        "planned_designs": planned_designs,
                        "rfd3_use": cfg["rfd3_use"],
                        "rfd3_use_ensemble": cfg["rfd3_use_ensemble"],
                        "rfd3_max_return_designs": cfg["rfd3_max_return_designs"],
                        "bioemu_use": cfg["bioemu_use"],
                        "bioemu_num_samples": cfg["bioemu_num_samples"],
                        "bioemu_max_return_structures": cfg["bioemu_max_return_structures"],
                        "af2_max_candidates_per_tier": 10,
                        "planned_max_af2": 30,
                    }
                )
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targets": targets,
        "arms": arms,
        "replicates": replicates,
        "jobs": jobs,
    }


def run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run backbone/ensemble ablation arms")
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--replicates", default="1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Resume incomplete existing run directories instead of skipping them.",
    )
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)

    targets = _parse_csv_list(args.targets, DEFAULT_TARGETS)
    arms = _parse_csv_list(args.arms, DEFAULT_ARMS)
    replicates = _parse_int_list(args.replicates, [1])
    for arm in arms:
        if arm not in ARM_CONFIGS:
            raise SystemExit(f"unknown arm: {arm}")

    manifest = _run_manifest(targets, arms, replicates)
    manifest_path = RESULTS_DIR / "backbone_ensemble_ablation_manifest.json"
    _write_json(manifest_path, manifest)
    print(f"wrote manifest: {manifest_path}")
    if args.dry_run:
        for item in manifest["jobs"]:
            print(
                f"[dry-run] {item['run_id']}: target={item['target']} arm={item['arm']} "
                f"planned_max_af2={item['planned_max_af2']}"
            )
        return 0

    from dotenv import load_dotenv
    from pipeline_mcp.app import build_runner

    load_dotenv(str(PROJECT_ROOT / "pipeline-mcp" / ".env"), override=True)
    runner = build_runner()
    for item in manifest["jobs"]:
        run_id = str(item["run_id"])
        run_dir = PROJECT_ROOT / "outputs" / run_id
        action = existing_run_action(
            run_dir,
            force=bool(args.force),
            resume_existing=bool(args.resume_existing),
        )
        if action == "skip_completed":
            print(f"[skip] {run_id}: completed")
            continue
        if action == "skip_existing":
            print(f"[skip] {run_id}: output exists")
            continue
        pdb_path = Path(str(item["pdb_path"]))
        try:
            if action == "resume":
                request = load_resume_request(run_dir)
                print(f"[resume] {run_id}: start_from={request.start_from}")
            else:
                request = build_request(
                    pdb_path.read_text(encoding="utf-8"),
                    str(item["arm"]),
                    seed=int(item["replicate"]),
                )
                print(f"[run] {run_id}")
            runner.run(request, run_id=run_id)
        except Exception as exc:
            print(f"[error] {run_id}: {exc}", file=sys.stderr)
            if args.stop_on_error:
                return 1
    return 0


def analyze_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize backbone/ensemble ablation outputs")
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--replicates", default="1")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    targets = _parse_csv_list(args.targets, DEFAULT_TARGETS)
    arms = _parse_csv_list(args.arms, DEFAULT_ARMS)
    replicates = _parse_int_list(args.replicates, [1])
    output_root = Path(args.output_root)

    rows = collect_all_rows(
        output_root=output_root,
        targets=targets,
        arms=arms,
        replicates=replicates,
    )
    if not rows:
        print("[warn] no ablation rows found")
    sequence_csv = RESULTS_DIR / "backbone_ensemble_ablation_sequences.csv"
    write_csv(sequence_csv, rows, fieldnames=SEQUENCE_FIELDS)
    print(f"wrote: {sequence_csv} ({len(rows)} rows)")

    summary_rows = build_summary(rows, top_k=int(args.top_k))
    summary_csv = RESULTS_DIR / "backbone_ensemble_ablation_summary.csv"
    write_csv(summary_csv, summary_rows, fieldnames=SUMMARY_FIELDS)
    print(f"wrote: {summary_csv} ({len(summary_rows)} rows)")

    paired_rows = _paired_tests(summary_rows)
    paired_csv = RESULTS_DIR / "backbone_ensemble_ablation_paired_tests.csv"
    write_csv(paired_csv, paired_rows, fieldnames=PAIRED_FIELDS)
    print(f"wrote: {paired_csv} ({len(paired_rows)} rows)")

    fig_path = FIG_DIR / "fig12_backbone_ensemble_ablation.png"
    _make_figure(summary_rows, fig_path)
    if summary_rows:
        print(f"wrote: {fig_path}")

    table_path = FIG_DIR / "table12_backbone_ensemble_ablation.tex"
    if summary_rows:
        _write_latex_table(summary_rows, table_path)
        print(f"wrote: {table_path}")
    return 0
