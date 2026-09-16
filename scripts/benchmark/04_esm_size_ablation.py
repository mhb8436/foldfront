#!/usr/bin/env python3
"""
Experiment 3 (Supplementary): does a larger ESM-2 model improve the surrogate?

Trains RF (the production choice) at N=30 with two embedding sizes:
    320D - facebook/esm2_t6_8M_UR50D
    640D - facebook/esm2_t30_150M_UR50D

If the 640D file is missing the script exits gracefully so the rest of the
pipeline can ship without blocking on the slower CPU embedding job.

Output: data/benchmark/results/exp3_esm_size.parquet
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from _selection import select_train_indices

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
RESULTS_DIR = DATA_DIR / "results"
CSV_PATH = DATA_DIR / "cath_pilot_dataset.csv"

EMB_PATHS = {
    "320D (ESM-2 8M)": DATA_DIR / "cath_pilot_emb_320d.npy",
    "640D (ESM-2 150M)": DATA_DIR / "cath_pilot_emb_640d.npy",
}
SEEDS = [42, 123, 7, 2024, 31337]
N_TRAIN = 30
HOLDOUT = 20
TOP_K = 5
SELECTION = "kmeans"


def topk_recall(y_true: np.ndarray, y_pred: np.ndarray, k: int) -> float:
    if k > len(y_true):
        return float("nan")
    return len(set(np.argsort(y_true)[::-1][:k]) & set(np.argsort(y_pred)[::-1][:k])) / k


def bo_uplift(y_true: np.ndarray, y_pred: np.ndarray, k: int, seed: int) -> float:
    if k > len(y_true):
        return float("nan")
    pred_top = np.argsort(y_pred)[::-1][:k]
    rng = np.random.default_rng(seed)
    rand = rng.choice(len(y_true), size=k, replace=False)
    return float(y_true[pred_top].mean() - y_true[rand].mean())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default=str(RESULTS_DIR / "exp3_esm_size.parquet")
    )
    args = parser.parse_args()

    df = pd.read_csv(CSV_PATH)

    embeddings_by_label: dict[str, np.ndarray] = {}
    for label, path in EMB_PATHS.items():
        if not path.exists():
            print(f"[skip] {label}: {path} missing - skipping ESM-size ablation",
                  file=sys.stderr)
            continue
        emb = np.load(path)
        if emb.shape[0] != len(df):
            print(f"[skip] {label}: shape {emb.shape} != csv {len(df)}",
                  file=sys.stderr)
            continue
        embeddings_by_label[label] = emb
        print(f"loaded {label}: shape {emb.shape}")

    if len(embeddings_by_label) < 2:
        print("Need both 320D and 640D embeddings to run ablation.", file=sys.stderr)
        return 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    started = time.time()
    targets = sorted(df["target"].unique())

    for surrogate in ["plddt", "soluprot"]:
        for tgt in targets:
            mask = (df["target"] == tgt) & df[surrogate].notna()
            sub = df[mask].reset_index(drop=True)
            indices = df.index[mask].to_numpy()
            y_full = sub[surrogate].to_numpy(dtype=np.float64)
            n_avail = len(sub)
            if n_avail < N_TRAIN + HOLDOUT:
                continue

            for seed in SEEDS:
                rng = np.random.default_rng(seed)
                perm = rng.permutation(n_avail)
                test_idx = perm[-HOLDOUT:]
                pool_idx = perm[:-HOLDOUT]
                y_te = y_full[test_idx]

                for label, emb in embeddings_by_label.items():
                    X = emb[indices]
                    X_pool = X[pool_idx]
                    local_train = select_train_indices(
                        X_pool, N_TRAIN, seed, SELECTION
                    )
                    train_idx = pool_idx[local_train]
                    X_tr = X[train_idx]
                    X_te = X[test_idx]
                    y_tr = y_full[train_idx]
                    if np.std(y_tr) < 1e-8:
                        continue
                    rf = RandomForestRegressor(
                        n_estimators=100, random_state=seed, n_jobs=1
                    )
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        rf.fit(X_tr, y_tr)
                        pred = rf.predict(X_te)

                    if np.std(pred) > 0 and np.std(y_te) > 0:
                        rho, _ = stats.spearmanr(y_te, pred)
                        rho = float(rho) if np.isfinite(rho) else float("nan")
                    else:
                        rho = float("nan")

                    rows.append(
                        {
                            "selection": SELECTION,
                            "surrogate": surrogate,
                            "target": tgt,
                            "seed": seed,
                            "embedding": label,
                            "n_train": N_TRAIN,
                            "spearman": rho,
                            "r2": float(r2_score(y_te, pred)),
                            "mae": float(mean_absolute_error(y_te, pred)),
                            "top5_recall": topk_recall(y_te, pred, TOP_K),
                            "bo_uplift_top5": bo_uplift(y_te, pred, TOP_K, seed),
                        }
                    )
            elapsed = time.time() - started
            print(f"  done {surrogate}/{tgt} elapsed={elapsed:.0f}s")

    out_df = pd.DataFrame(rows)
    out_df.to_parquet(args.out, index=False)
    print(f"Saved {args.out} ({len(out_df)} rows)")

    print("\n=== Mean metrics by embedding ===")
    print(
        out_df.groupby(["surrogate", "embedding"])[["spearman", "top5_recall", "bo_uplift_top5"]]
        .mean()
        .round(4)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
