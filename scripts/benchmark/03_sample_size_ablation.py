#!/usr/bin/env python3
"""
Experiment 2: Sample-size ablation to justify N=30 as conservative.

For each (surrogate, target, seed, N), train RF + Top-3 alternatives and
record metrics on the held-out pool. The remaining-pool size is fixed at 20
so that BO uplift / Top-K recall numbers are comparable across N.

N grid: {5, 10, 20, 30, 50, 80}
    N=80 + 20 holdout requires at least 100 labels.
Models: RF, XGBoost, LightGBM, Ridge (production-supported alternatives).

Output parquet schema:
    surrogate, target, seed, model, n_train, n_test, <metric columns>
"""

from __future__ import annotations

import argparse
import time
import warnings
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler

import lightgbm as lgb
import xgboost as xgb

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _selection import select_train_indices

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
RESULTS_DIR = DATA_DIR / "results"
CSV_PATH = DATA_DIR / "cath_pilot_dataset.csv"
EMB_PATH_320 = DATA_DIR / "cath_pilot_emb_320d.npy"

SEEDS = [42, 123, 7, 2024, 31337]
N_GRID = [5, 10, 20, 30, 50, 80]
HOLDOUT = 20
SURROGATE_TARGETS = ["plddt", "soluprot"]
TOP_K_VALUES = [5, 20]
SELECTION_STRATEGIES = ["random", "kmeans"]


def make_models(seed: int) -> dict[str, object]:
    return {
        "RF": RandomForestRegressor(n_estimators=100, random_state=seed, n_jobs=1),
        "XGBoost": xgb.XGBRegressor(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            random_state=seed,
            n_jobs=1,
            verbosity=0,
        ),
        "LightGBM": lgb.LGBMRegressor(
            n_estimators=100,
            num_leaves=7,
            min_data_in_leaf=2,
            min_data_in_bin=1,
            learning_rate=0.05,
            random_state=seed,
            n_jobs=1,
            verbose=-1,
        ),
        "Ridge": Ridge(alpha=1.0, random_state=seed),
    }


def needs_scaling(name: str) -> bool:
    return name == "Ridge"


def fit_predict(model, X_tr, y_tr, X_te, scale: bool):
    if scale:
        sc = StandardScaler().fit(X_tr)
        X_tr, X_te = sc.transform(X_tr), sc.transform(X_te)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X_tr, y_tr)
        return model.predict(X_te)


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


def evaluate(y_true: np.ndarray, y_pred: np.ndarray, seed: int) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if len(y_true) >= 2 and np.std(y_true) > 0 and np.std(y_pred) > 0:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=stats.ConstantInputWarning)
            rho, _ = stats.spearmanr(y_true, y_pred)
        metrics["spearman"] = float(rho) if np.isfinite(rho) else float("nan")
    else:
        metrics["spearman"] = float("nan")
    metrics["r2"] = float(r2_score(y_true, y_pred))
    metrics["mae"] = float(mean_absolute_error(y_true, y_pred))
    for k in TOP_K_VALUES:
        metrics[f"top{k}_recall"] = topk_recall(y_true, y_pred, k)
        metrics[f"bo_uplift_top{k}"] = bo_uplift(y_true, y_pred, k, seed)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emb", default=str(EMB_PATH_320))
    parser.add_argument(
        "--out",
        default=str(RESULTS_DIR / "exp2_sample_size.parquet"),
    )
    args = parser.parse_args()

    df = pd.read_csv(CSV_PATH)
    embeddings = np.load(args.emb)
    if embeddings.shape[0] != len(df):
        raise SystemExit("Embedding/CSV row mismatch")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    targets = sorted(df["target"].unique())
    print(f"Targets: {len(targets)}, Seeds: {len(SEEDS)}, N grid: {N_GRID}", flush=True)

    rows: list[dict] = []
    started = time.time()
    print(f"Selections: {SELECTION_STRATEGIES}, N grid: {N_GRID}", flush=True)

    for surrogate in SURROGATE_TARGETS:
        for tgt in targets:
            mask = (df["target"] == tgt) & df[surrogate].notna()
            sub = df[mask].reset_index(drop=True)
            indices = df.index[mask].to_numpy()
            X_full = embeddings[indices]
            y_full = sub[surrogate].to_numpy(dtype=np.float64)
            n_avail = len(sub)

            for seed in SEEDS:
                rng = np.random.default_rng(seed)
                perm = rng.permutation(n_avail)
                test_idx = perm[-HOLDOUT:]
                pool_idx = perm[:-HOLDOUT]
                X_pool = X_full[pool_idx]
                y_pool = y_full[pool_idx]
                X_te = X_full[test_idx]
                y_te = y_full[test_idx]

                for selection in SELECTION_STRATEGIES:
                    for n_train in N_GRID:
                        if n_train > len(pool_idx):
                            continue
                        local_train = select_train_indices(
                            X_pool, n_train, seed, selection
                        )
                        X_tr = X_pool[local_train]
                        y_tr = y_pool[local_train]

                        if np.std(y_tr) < 1e-8:
                            continue

                        for name, model in make_models(seed).items():
                            pred = fit_predict(
                                model, X_tr, y_tr, X_te, needs_scaling(name)
                            )
                            metrics = evaluate(y_te, pred, seed)
                            rows.append(
                                {
                                    "selection": selection,
                                    "surrogate": surrogate,
                                    "target": tgt,
                                    "seed": seed,
                                    "n_train": n_train,
                                    "n_test": HOLDOUT,
                                    "model": name,
                                    **metrics,
                                }
                            )
            elapsed = time.time() - started
            print(
                f"  done {surrogate}/{tgt} (n_avail={n_avail}, elapsed={elapsed:.0f}s)",
                flush=True,
            )

    out_df = pd.DataFrame(rows)
    out_df.to_parquet(args.out, index=False)
    print(f"Saved {args.out} ({len(out_df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
