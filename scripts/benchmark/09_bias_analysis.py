#!/usr/bin/env python3
"""
Replicate the bias-analysis methodology of
``docs/2026-04-24-meta-surrogate-bias-analysis-ko.md`` on the benchmark set.

For each (target, seed, selection_strategy):
    1. Split ProteinMPNN designs into 30 train + held-out test candidates
       (selection ∈ {random, kmeans})
    2. Identify the training-set best (max actual pLDDT in train subset)
    3. Train Random Forest, predict pLDDT on the 90 test designs
    4. Take the surrogate's predicted top-10
    5. Take the true top-10 (max actual pLDDT) — the upper bound

Two bias metrics, computed on amino-acid identity since all 120 designs
of a target share length and a fixed scaffold:
    overfit_identity = mean per-residue identity(top-10 candidate, train_best)
    diversity_identity = mean pairwise identity within top-10
"""

from __future__ import annotations

import sys
import warnings
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.ensemble import RandomForestRegressor

sys.path.insert(0, str(Path(__file__).parent))
from _selection import select_train_indices

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
RESULTS_DIR = DATA_DIR / "results"
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"

CSV = DATA_DIR / "cath_pilot_dataset.csv"
EMB = DATA_DIR / "cath_pilot_emb_320d.npy"
SEEDS = [42, 123, 7, 2024, 31337]
N_TRAIN = 30
TOP_K_SET = (5, 10)
SELECTIONS = ["random", "kmeans"]


def per_residue_identity(a: str, b: str) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    matches = sum(1 for i in range(n) if a[i] == b[i])
    return matches / max(len(a), len(b))


def mean_identity_to_anchor(seqs: list[str], anchor: str) -> float:
    if not seqs:
        return float("nan")
    return float(np.mean([per_residue_identity(s, anchor) for s in seqs]))


def mean_pairwise_identity(seqs: list[str]) -> float:
    n = len(seqs)
    if n < 2:
        return float("nan")
    vals: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            vals.append(per_residue_identity(seqs[i], seqs[j]))
    return float(np.mean(vals))


def main() -> int:
    df = pd.read_csv(CSV).reset_index(drop=True)
    embeddings = np.load(EMB)
    if embeddings.shape[0] != len(df):
        raise SystemExit("embedding/CSV row mismatch")

    targets = sorted(df["target"].unique())
    rows: list[dict] = []

    for tgt in targets:
        mask = (df["target"] == tgt) & df["plddt"].notna()
        sub = df[mask].reset_index(drop=True)
        idx = df.index[mask].to_numpy()
        if len(sub) < N_TRAIN + max(TOP_K_SET) + 5:
            continue
        X_full = embeddings[idx]
        y_full = sub["plddt"].to_numpy(dtype=np.float64)
        seqs_full = sub["sequence"].astype(str).tolist()

        for seed in SEEDS:
            rng = np.random.default_rng(seed)
            order = rng.permutation(len(sub))

            for selection in SELECTIONS:
                tr_local = select_train_indices(X_full, N_TRAIN, seed, selection)
                tr_set = set(tr_local.tolist())
                te_local = np.array([i for i in order if int(i) not in tr_set])
                if te_local.size < max(TOP_K_SET):
                    continue

                X_tr = X_full[tr_local]
                y_tr = y_full[tr_local]
                X_te = X_full[te_local]
                y_te = y_full[te_local]

                if np.std(y_tr) < 1e-8:
                    continue

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    rf = RandomForestRegressor(n_estimators=100, random_state=seed, n_jobs=1)
                    rf.fit(X_tr, y_tr)
                    pred = rf.predict(X_te)

                train_best_global = tr_local[int(np.argmax(y_tr))]
                anchor_seq = seqs_full[train_best_global]

                row: dict = {
                    "target": tgt,
                    "seed": int(seed),
                    "selection": selection,
                }
                for k in TOP_K_SET:
                    rf_top_local = np.argsort(pred)[::-1][:k]
                    true_top_local = np.argsort(y_te)[::-1][:k]
                    rand_idx = rng.choice(len(y_te), size=k, replace=False)

                    rf_seqs = [seqs_full[int(te_local[i])] for i in rf_top_local]
                    true_seqs = [seqs_full[int(te_local[i])] for i in true_top_local]
                    rand_seqs = [seqs_full[int(te_local[i])] for i in rand_idx]

                    row[f"rf_overfit_identity_top{k}"] = mean_identity_to_anchor(rf_seqs, anchor_seq)
                    row[f"true_overfit_identity_top{k}"] = mean_identity_to_anchor(true_seqs, anchor_seq)
                    row[f"rf_internal_identity_top{k}"] = mean_pairwise_identity(rf_seqs)
                    row[f"true_internal_identity_top{k}"] = mean_pairwise_identity(true_seqs)
                    row[f"rand_internal_identity_top{k}"] = mean_pairwise_identity(rand_seqs)
                    row[f"rf_top{k}_mean_plddt"] = float(y_te[rf_top_local].mean())
                    row[f"true_top{k}_mean_plddt"] = float(y_te[true_top_local].mean())
                    row[f"rand_top{k}_mean_plddt"] = float(y_te[rand_idx].mean())
                rows.append(row)
        print(f"  done {tgt}")

    out = pd.DataFrame(rows)
    out_path = RESULTS_DIR / "bias_analysis_per_seed.csv"
    out.to_csv(out_path, index=False)

    metric_cols = [c for c in out.columns
                   if c.startswith(("rf_", "true_", "rand_"))]
    summary = out.groupby("selection")[metric_cols].mean().round(4)
    summary_path = RESULTS_DIR / "bias_analysis_summary.csv"
    summary.to_csv(summary_path)

    print(f"\n=== Bias-analysis summary (means across {len(targets)} targets x 5 seeds) ===")
    for k in TOP_K_SET:
        cols = [c for c in metric_cols if c.endswith(f"_top{k}") or c.endswith(f"top{k}_mean_plddt")]
        print(f"\n--- Top-{k} ---")
        print(summary[cols].to_string())

    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.6))

    for row_idx, k in enumerate(TOP_K_SET):
        overfit_col = f"rf_overfit_identity_top{k}"
        internal_col = f"rf_internal_identity_top{k}"
        true_overfit = out[f"true_overfit_identity_top{k}"].mean()
        true_internal = out[f"true_internal_identity_top{k}"].mean()
        rand_internal = out[f"rand_internal_identity_top{k}"].mean()

        grouped = (
            out.groupby(["target", "selection"])[[overfit_col, internal_col]]
            .mean().reset_index()
        )

        ax_left = axes[row_idx, 0]
        sns.boxplot(data=grouped, x="selection", y=overfit_col, ax=ax_left,
                    order=SELECTIONS,
                    palette={"random": "#bbbbbb", "kmeans": "#1f77b4"},
                    boxprops=dict(alpha=0.7))
        sns.stripplot(data=grouped, x="selection", y=overfit_col, order=SELECTIONS,
                      ax=ax_left, size=4, color="#222")
        ax_left.axhline(true_overfit, color="#d62728", linestyle="--", linewidth=1.2,
                        label=f"True optimal Top-{k} ({true_overfit:.3f})")
        ax_left.set_title(f"Overfitting to training-set best (Top-{k} → train best)")
        ax_left.set_xlabel("training-set selection")
        ax_left.set_ylabel("mean per-residue identity")
        ax_left.legend(loc="lower left", fontsize=9)

        ax_right = axes[row_idx, 1]
        sns.boxplot(data=grouped, x="selection", y=internal_col, ax=ax_right,
                    order=SELECTIONS,
                    palette={"random": "#bbbbbb", "kmeans": "#1f77b4"},
                    boxprops=dict(alpha=0.7))
        sns.stripplot(data=grouped, x="selection", y=internal_col, order=SELECTIONS,
                      ax=ax_right, size=4, color="#222")
        ax_right.axhline(true_internal, color="#d62728", linestyle="--", linewidth=1.2,
                         label=f"True optimal Top-{k} ({true_internal:.3f})")
        ax_right.axhline(rand_internal, color="#888", linestyle=":", linewidth=1.2,
                         label=f"Random Top-{k} ({rand_internal:.3f})")
        ax_right.set_title(f"Diversity loss inside selected Top-{k} (pairwise identity)")
        ax_right.set_xlabel("training-set selection")
        ax_right.set_ylabel("mean pairwise identity")
        ax_right.legend(loc="lower left", fontsize=9)

    fig.suptitle(
        f"Bias analysis on {len(targets)} CATH targets: K-Means vs Random selection (RF, N=30) - "
        "Top-5 (top row) and Top-10 (bottom row)",
        fontsize=11, y=1.00,
    )
    fig.tight_layout()
    fig_path = FIG_DIR / "fig6_bias_analysis.png"
    fig.savefig(fig_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved {fig_path}")
    print(f"Saved {out_path}, {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
