#!/usr/bin/env python3
"""Evaluate pooled surrogate scaling from completed CATH artifacts.

The analysis uses already-computed CATH design artifacts only. For each
target-tier unit, 30 AF2-labelled designs are used for target calibration and
the remaining designs are held out. A ridge surrogate is then trained either on
the target calibration labels alone or on the same labels plus pooled labels
from other CATH targets. Labels are centered within each target-tier training
set so that the pooled model learns residual ranking signals rather than target
baseline pLDDT differences.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sys
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

DEFAULT_RESULTS = PROJECT_ROOT / "data" / "benchmark" / "results"
DEFAULT_FIGURES = PROJECT_ROOT / "figures" / "benchmark"
AA = "ACDEFGHIKLMNPQRSTVWY"
AA_INDEX = {aa: idx for idx, aa in enumerate(AA)}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    header: str | None = None
    seq_parts: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(seq_parts)))
            header = line[1:].strip()
            seq_parts = []
        else:
            seq_parts.append(line)
    if header is not None:
        records.append((header, "".join(seq_parts)))
    return records


def _record_id(header: str) -> str:
    return str(header or "").split()[0].strip()


def _header_float(header: str, key: str, default: float = 0.0) -> float:
    match = re.search(rf"{re.escape(key)}=([-+0-9.eE]+)", str(header or ""))
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


def _sequence_features(sequence: str, header: str, tier: str) -> np.ndarray:
    clean = "".join(ch for ch in str(sequence or "").upper() if ch.isalpha())
    length = max(1, len(clean))
    counts = np.zeros(len(AA), dtype=np.float64)
    for ch in clean:
        idx = AA_INDEX.get(ch)
        if idx is not None:
            counts[idx] += 1.0
    freqs = counts / float(length)
    grouped = np.asarray(
        [
            length / 500.0,
            sum(clean.count(ch) for ch in "DEKRH") / float(length),
            sum(clean.count(ch) for ch in "AILMFWVY") / float(length),
            sum(clean.count(ch) for ch in "STNQCY") / float(length),
            sum(clean.count(ch) for ch in "DE") / float(length),
            sum(clean.count(ch) for ch in "KRH") / float(length),
            sum(clean.count(ch) for ch in "FWY") / float(length),
            clean.count("P") / float(length),
            clean.count("G") / float(length),
            clean.count("C") / float(length),
            float(tier) / 100.0,
            _header_float(header, "score"),
            _header_float(header, "global_score"),
            _header_float(header, "seq_recovery"),
        ],
        dtype=np.float64,
    )
    return np.concatenate([grouped, freqs])


def _stable_seed(text: str, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{text}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _collect_rows(cath_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_dir in sorted(cath_root.glob("cath_*")):
        status = _load_json(run_dir / "status.json")
        if str(status.get("state") or "").lower() != "completed":
            continue
        tiers_root = run_dir / "tiers"
        if not tiers_root.is_dir():
            continue
        for tier_dir in sorted(tiers_root.glob("*")):
            if not tier_dir.is_dir():
                continue
            scores_path = tier_dir / "af2_scores.json"
            fasta_path = tier_dir / "designs.fasta"
            if not scores_path.exists() or not fasta_path.exists():
                continue
            score_payload = _load_json(scores_path)
            plddt_scores = score_payload.get("scores") if isinstance(score_payload.get("scores"), dict) else {}
            rmsd_scores = score_payload.get("rmsd_scores") if isinstance(score_payload.get("rmsd_scores"), dict) else {}
            for header, sequence in _parse_fasta(fasta_path):
                seq_id = _record_id(header)
                if seq_id.lower() == "input":
                    continue
                plddt = plddt_scores.get(f"target:{seq_id}")
                if not isinstance(plddt, (int, float)) or not math.isfinite(float(plddt)) or float(plddt) <= 0:
                    continue
                rmsd = rmsd_scores.get(f"target:{seq_id}")
                rows.append(
                    {
                        "target": run_dir.name,
                        "tier": tier_dir.name,
                        "unit": f"{run_dir.name}|{tier_dir.name}",
                        "sequence_id": seq_id,
                        "sequence": sequence,
                        "header": header,
                        "plddt": float(plddt),
                        "rmsd_ca": float(rmsd) if isinstance(rmsd, (int, float)) and math.isfinite(float(rmsd)) else "",
                    }
                )
    return rows


def _composition_feature_matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.vstack(
        [
            _sequence_features(
                str(row["sequence"]),
                str(row["header"]),
                str(row["tier"]),
            )
            for row in rows
        ]
    ).astype(np.float64)


def _esm_feature_matrix(
    rows: list[dict[str, Any]],
    *,
    esm_url: str,
    cache_path: Path,
    batch_size: int,
    max_length: int,
    request_chunk_size: int,
) -> np.ndarray:
    from pipeline_mcp.clients.esm_embedding import LocalHTTPESMEmbeddingClient

    sequences = [str(row["sequence"]) for row in rows]
    digest = hashlib.sha256("\n".join(sequences).encode("utf-8")).hexdigest()
    if cache_path.exists():
        with np.load(cache_path, allow_pickle=False) as loaded:
            cached_digest = str(loaded["digest"])
            if cached_digest == digest:
                return np.asarray(loaded["embeddings"], dtype=np.float64)

    client = LocalHTTPESMEmbeddingClient(
        base_url=esm_url,
        timeout_s=float(os.environ.get("ESM_EMBEDDING_TIMEOUT_S", "21600") or "21600"),
        batch_size=int(batch_size),
        max_length=int(max_length),
        request_chunk_size=int(request_chunk_size),
    )
    embeddings = np.asarray(client.embed(sequences), dtype=np.float64)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, embeddings=embeddings, digest=np.asarray(digest))
    return embeddings


def _feature_matrix(
    rows: list[dict[str, Any]],
    *,
    feature_mode: str,
    esm_url: str | None,
    esm_cache: Path,
    esm_batch_size: int,
    esm_max_length: int,
    esm_request_chunk_size: int,
) -> tuple[np.ndarray, str]:
    mode = str(feature_mode or "composition").strip().lower()
    composition = _composition_feature_matrix(rows)
    if mode == "composition":
        return composition, "composition_mpnn_metadata"
    if not esm_url:
        raise RuntimeError("--esm-url or ESM_EMBEDDING_URL is required for ESM feature modes")
    esm = _esm_feature_matrix(
        rows,
        esm_url=esm_url,
        cache_path=esm_cache,
        batch_size=esm_batch_size,
        max_length=esm_max_length,
        request_chunk_size=esm_request_chunk_size,
    )
    if mode == "esm":
        return esm, "esm2_8m_mean"
    if mode == "esm_plus_composition":
        return np.hstack([esm, composition]).astype(np.float64), "esm2_8m_mean_plus_composition_mpnn_metadata"
    raise ValueError("feature_mode must be one of: composition, esm, esm_plus_composition")


def _safe_spearman(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 3:
        return float("nan")
    y_rank = _rankdata(y_true)
    p_rank = _rankdata(y_pred)
    if float(np.std(y_rank)) == 0.0 or float(np.std(p_rank)) == 0.0:
        return float("nan")
    return float(np.corrcoef(y_rank, p_rank)[0, 1])


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    sorted_values = values[order]
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0
        start = end
    return ranks


def _fit_predict_ridge(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    *,
    alpha: float,
) -> np.ndarray:
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        Ridge(alpha=float(alpha), random_state=0),
    )
    model.fit(x_train, y_train)
    return np.asarray(model.predict(x_test), dtype=np.float64)


def _unit_splits(
    rows: list[dict[str, Any]],
    *,
    train_n: int,
    min_eval_n: int,
    seed: int,
) -> tuple[dict[str, list[int]], dict[str, list[int]], dict[str, list[int]]]:
    by_unit: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        by_unit.setdefault(str(row["unit"]), []).append(idx)
    usable = {
        unit: indices
        for unit, indices in by_unit.items()
        if len(indices) >= int(train_n) + int(min_eval_n)
    }
    train: dict[str, list[int]] = {}
    test: dict[str, list[int]] = {}
    for unit, indices in sorted(usable.items()):
        rng = random.Random(_stable_seed(unit, seed))
        shuffled = list(indices)
        rng.shuffle(shuffled)
        train[unit] = shuffled[: int(train_n)]
        test[unit] = shuffled[int(train_n) :]
    return usable, train, test


def _choose_pool_targets(
    all_targets: list[str],
    heldout_target: str,
    pool_size: int,
    seed: int,
) -> list[str]:
    candidates = [target for target in all_targets if target != heldout_target]
    rng = random.Random(_stable_seed(f"{heldout_target}:{pool_size}", seed))
    rng.shuffle(candidates)
    return candidates[: min(int(pool_size), len(candidates))]


def _evaluate_prediction(
    *,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    top_k: int,
) -> dict[str, float]:
    top = max(1, min(int(top_k), len(y_true)))
    pred_top_idx = np.argsort(y_pred)[-top:]
    oracle_top_idx = np.argsort(y_true)[-top:]
    selected_mean = float(np.mean(y_true[pred_top_idx]))
    oracle_mean = float(np.mean(y_true[oracle_top_idx]))
    random_mean = float(np.mean(y_true))
    return {
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "spearman": _safe_spearman(y_true, y_pred),
        "topk_selected_mean": selected_mean,
        "topk_oracle_mean": oracle_mean,
        "topk_random_mean": random_mean,
        "topk_uplift": selected_mean - random_mean,
        "topk_regret": oracle_mean - selected_mean,
    }


def _run_scaling(
    rows: list[dict[str, Any]],
    x: np.ndarray,
    *,
    train_n: int,
    min_eval_n: int,
    top_k: int,
    pool_sizes: list[int],
    seed: int,
    ridge_alpha: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    usable, train_by_unit, test_by_unit = _unit_splits(
        rows,
        train_n=train_n,
        min_eval_n=min_eval_n,
        seed=seed,
    )
    targets = sorted({str(rows[indices[0]]["target"]) for indices in usable.values()})
    units_by_target: dict[str, list[str]] = {}
    for unit in usable:
        target = str(rows[usable[unit][0]]["target"])
        units_by_target.setdefault(target, []).append(unit)

    y = np.asarray([float(row["plddt"]) for row in rows], dtype=np.float64)
    unit_train_mean = {
        unit: float(np.mean(y[indices]))
        for unit, indices in train_by_unit.items()
    }
    residual_y = np.asarray(
        [float(row["plddt"]) - unit_train_mean.get(str(row["unit"]), 0.0) for row in rows],
        dtype=np.float64,
    )

    results: list[dict[str, Any]] = []
    for heldout_target in targets:
        heldout_units = units_by_target.get(heldout_target, [])
        if not heldout_units:
            continue
        for pool_size in pool_sizes:
            pool_targets = _choose_pool_targets(targets, heldout_target, pool_size, seed)
            pool_train_idx = [
                idx
                for unit, indices in train_by_unit.items()
                if str(rows[indices[0]]["target"]) in set(pool_targets)
                for idx in indices
            ]
            for unit in heldout_units:
                target_train_idx = train_by_unit[unit]
                test_idx = test_by_unit[unit]
                if len(test_idx) < min_eval_n:
                    continue
                y_true = y[test_idx]
                x_test = x[test_idx]
                center = unit_train_mean[unit]

                strategies: list[tuple[str, list[int], bool]] = [
                    ("target_mean", [], False),
                    ("target_only", target_train_idx, True),
                ]
                if pool_train_idx:
                    strategies.append(("pooled_prior", pool_train_idx, True))
                    strategies.append(("pooled_plus_target", target_train_idx + pool_train_idx, True))

                for strategy, train_idx, fit_model in strategies:
                    if not fit_model:
                        y_pred = np.full(len(test_idx), center, dtype=np.float64)
                    else:
                        y_resid_pred = _fit_predict_ridge(
                            x[train_idx],
                            residual_y[train_idx],
                            x_test,
                            alpha=ridge_alpha,
                        )
                        y_pred = center + y_resid_pred
                    metric_row = _evaluate_prediction(
                        y_true=y_true,
                        y_pred=y_pred,
                        top_k=top_k,
                    )
                    metric_row.update(
                        {
                            "strategy": strategy,
                            "pool_size_targets": int(pool_size),
                            "heldout_target": heldout_target,
                            "unit": unit,
                            "tier": str(rows[test_idx[0]]["tier"]),
                            "n_train_target": len(target_train_idx),
                            "n_pool_train": len(pool_train_idx),
                            "n_eval": len(test_idx),
                        }
                    )
                    results.append(metric_row)

    metadata = {
        "input_rows": len(rows),
        "usable_units": len(usable),
        "usable_targets": len(targets),
        "train_n": int(train_n),
        "min_eval_n": int(min_eval_n),
        "top_k": int(top_k),
        "pool_sizes": pool_sizes,
        "model": "ridge_residual_centered",
        "ridge_alpha": float(ridge_alpha),
    }
    return results, metadata


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["strategy"]), int(row["pool_size_targets"])), []).append(row)
    out: list[dict[str, Any]] = []
    for (strategy, pool_size), items in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        targets = {str(item["heldout_target"]) for item in items}
        units = {str(item["unit"]) for item in items}
        base = {
            "strategy": strategy,
            "pool_size_targets": pool_size,
            "n_targets": len(targets),
            "n_units": len(units),
        }
        for metric in ("mae", "spearman", "topk_uplift", "topk_regret"):
            values = np.asarray(
                [
                    float(item[metric])
                    for item in items
                    if isinstance(item.get(metric), (int, float)) and math.isfinite(float(item[metric]))
                ],
                dtype=np.float64,
            )
            base[f"{metric}_mean"] = float(np.mean(values)) if len(values) else float("nan")
            base[f"{metric}_median"] = float(np.median(values)) if len(values) else float("nan")
        out.append(base)
    return out


def _write_table(path: Path, summary_rows: list[dict[str, Any]], *, pool_sizes: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        row
        for row in summary_rows
        if row["strategy"] in {"target_only", "pooled_plus_target"}
        and int(row["pool_size_targets"]) in set(pool_sizes)
        and not (row["strategy"] == "target_only" and int(row["pool_size_targets"]) != min(pool_sizes))
    ]
    lines = [
        r"\begin{tabular}{llrrrr}",
        r"\toprule",
        r"Training source & Pooled targets & Units & MAE & Top-K regret & Spearman \\",
        r"\midrule",
    ]
    label = {
        "target_only": "Target calibration only",
        "pooled_plus_target": "Pooled prior + target calibration",
    }
    for row in rows:
        lines.append(
            "{} & {} & {} & {:.3f} & {:.3f} & {:.3f} \\\\".format(
                label.get(str(row["strategy"]), str(row["strategy"])),
                int(row["pool_size_targets"]),
                int(row["n_units"]),
                float(row["mae_mean"]),
                float(row["topk_regret_mean"]),
                float(row["spearman_median"]) if math.isfinite(float(row["spearman_median"])) else float("nan"),
            )
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_figure(path: Path, summary_rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    strategies = ["target_only", "pooled_plus_target"]
    labels = {
        "target_only": "Target only",
        "pooled_plus_target": "Pooled + target",
    }
    colors = {
        "target_only": "#0072B2",
        "pooled_plus_target": "#D55E00",
    }
    data = {
        (str(row["strategy"]), int(row["pool_size_targets"])): row
        for row in summary_rows
    }
    pool_sizes = sorted({int(row["pool_size_targets"]) for row in summary_rows})

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 9,
            "axes.titlesize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7), constrained_layout=True)
    panels = [
        ("mae_mean", "Held-out MAE (pLDDT)", "A"),
        ("topk_regret_mean", "Top-3 regret (pLDDT)", "B"),
    ]
    for ax, (metric, ylabel, panel) in zip(axes, panels, strict=True):
        for strategy in strategies:
            xs: list[int] = []
            ys: list[float] = []
            for pool in pool_sizes:
                row = data.get((strategy, pool))
                if not row:
                    continue
                value = float(row[metric])
                if math.isfinite(value):
                    xs.append(pool)
                    ys.append(value)
            if xs:
                ax.plot(
                    xs,
                    ys,
                    marker="o",
                    linewidth=1.8,
                    markersize=4,
                    color=colors[strategy],
                    label=labels[strategy],
                )
        ax.set_xlabel("Pooled training targets")
        ax.set_ylabel(ylabel)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.25, linewidth=0.6)
        ax.text(-0.16, 1.08, panel, transform=ax.transAxes, fontweight="bold", fontsize=10)
    axes[0].legend(frameon=False)
    fig.savefig(path, dpi=300)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)


def _parse_pool_sizes(text: str) -> list[int]:
    return [int(item.strip()) for item in str(text or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cath-root", default="/opt/protein_pipeline/cath_outputs")
    parser.add_argument("--train-n", type=int, default=30)
    parser.add_argument("--min-eval-n", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--pool-sizes", default="0,5,10,20,30")
    parser.add_argument("--seed", type=int, default=20260520)
    parser.add_argument("--ridge-alpha", type=float, default=10.0)
    parser.add_argument(
        "--feature-mode",
        choices=["composition", "esm", "esm_plus_composition"],
        default="composition",
    )
    parser.add_argument("--esm-url", default=os.environ.get("ESM_EMBEDDING_URL", ""))
    parser.add_argument(
        "--esm-cache",
        default=str(DEFAULT_RESULTS / "pooled_surrogate_esm_embeddings.npz"),
    )
    parser.add_argument("--esm-batch-size", type=int, default=64)
    parser.add_argument("--esm-max-length", type=int, default=1024)
    parser.add_argument("--esm-request-chunk-size", type=int, default=1000)
    parser.add_argument(
        "--labels-out",
        default=str(DEFAULT_RESULTS / "pooled_surrogate_cath_labels.csv"),
    )
    parser.add_argument(
        "--metrics-out",
        default=str(DEFAULT_RESULTS / "pooled_surrogate_scaling_metrics.csv"),
    )
    parser.add_argument(
        "--summary-out",
        default=str(DEFAULT_RESULTS / "pooled_surrogate_scaling_summary.csv"),
    )
    parser.add_argument(
        "--metadata-out",
        default=str(DEFAULT_RESULTS / "pooled_surrogate_scaling_metadata.json"),
    )
    parser.add_argument(
        "--figure-out",
        default=str(DEFAULT_FIGURES / "fig9_pooled_surrogate_scaling.png"),
    )
    parser.add_argument(
        "--table-out",
        default=str(DEFAULT_FIGURES / "table8_pooled_surrogate_scaling.tex"),
    )
    args = parser.parse_args(argv)

    cath_root = Path(args.cath_root)
    rows = _collect_rows(cath_root)
    if not rows:
        raise RuntimeError(f"No usable CATH AF2 labels found under {cath_root}")
    x, feature_source = _feature_matrix(
        rows,
        feature_mode=str(args.feature_mode),
        esm_url=str(args.esm_url or "").strip() or None,
        esm_cache=Path(args.esm_cache),
        esm_batch_size=int(args.esm_batch_size),
        esm_max_length=int(args.esm_max_length),
        esm_request_chunk_size=int(args.esm_request_chunk_size),
    )
    for row in rows:
        row["feature_source"] = feature_source
    labels_rows = [
        {
            "target": row["target"],
            "tier": row["tier"],
            "unit": row["unit"],
            "sequence_id": row["sequence_id"],
            "plddt": row["plddt"],
            "rmsd_ca": row["rmsd_ca"],
            "feature_source": row["feature_source"],
        }
        for row in rows
    ]
    _write_csv(Path(args.labels_out), labels_rows)

    metrics, metadata = _run_scaling(
        rows,
        x,
        train_n=int(args.train_n),
        min_eval_n=int(args.min_eval_n),
        top_k=int(args.top_k),
        pool_sizes=_parse_pool_sizes(args.pool_sizes),
        seed=int(args.seed),
        ridge_alpha=float(args.ridge_alpha),
    )
    summary = _summarize(metrics)
    metadata.update(
        {
            "cath_root": str(cath_root),
            "labels_out": str(Path(args.labels_out)),
            "metrics_out": str(Path(args.metrics_out)),
            "summary_out": str(Path(args.summary_out)),
            "feature_count": int(x.shape[1]),
            "feature_source": feature_source,
        }
    )
    _write_csv(Path(args.metrics_out), metrics)
    _write_csv(Path(args.summary_out), summary)
    Path(args.metadata_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metadata_out).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    _make_figure(Path(args.figure_out), summary)
    _write_table(Path(args.table_out), summary, pool_sizes=[0, 10, 20, 30])
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
