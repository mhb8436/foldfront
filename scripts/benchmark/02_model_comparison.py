#!/usr/bin/env python3
"""
Experiment 1: Compare RF against 7 alternative surrogates.

Per-target leave-out CV with 5 random seeds:
    For each surrogate_target in {pLDDT, SoluProt}:
        For each CATH target in the benchmark CSV:
            For each seed in [42, 123, 7, 2024, 31337]:
                shuffle the target's ~120 labels
                split: 30 train / remainder test
                fit each model on (X_train, y_train)
                evaluate on the held-out pool

Output: parquet with one row per (surrogate, target, seed, model, metric).
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
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
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
N_TRAIN_DEFAULT = 30
SURROGATE_TARGETS = ["plddt", "soluprot"]
TOP_K_VALUES = [5, 20]
SELECTION_STRATEGIES = ["random", "kmeans"]


def make_models(seed: int) -> dict[str, object]:
    """Instantiate all surrogate models with a fixed seed where applicable."""
    return {
        "RF": RandomForestRegressor(
            n_estimators=100, random_state=seed, n_jobs=1
        ),
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
        "GP-RBF": GaussianProcessRegressor(
            kernel=RBF() + WhiteKernel(),
            normalize_y=True,
            random_state=seed,
            n_restarts_optimizer=2,
        ),
        "MLP": MLPRegressor(
            hidden_layer_sizes=(64,),
            alpha=1e-2,
            max_iter=2000,
            random_state=seed,
            early_stopping=False,
        ),
        "Ridge": Ridge(alpha=1.0, random_state=seed),
        "KNN": KNeighborsRegressor(n_neighbors=5),
        "Random": None,
    }


def fit_predict(model, X_train, y_train, X_test, seed: int, scale: bool):
    """Train one model and return predictions on the held-out pool."""
    if model is None:
        rng = np.random.default_rng(seed)
        return rng.normal(loc=y_train.mean(), scale=y_train.std() + 1e-6, size=len(X_test))

    if scale:
        scaler = StandardScaler().fit(X_train)
        X_train = scaler.transform(X_train)
        X_test = scaler.transform(X_test)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X_train, y_train)
        return model.predict(X_test)


def needs_scaling(model_name: str) -> bool:
    return model_name in {"GP-RBF", "MLP", "Ridge", "KNN"}


def topk_recall(y_true: np.ndarray, y_pred: np.ndarray, k: int) -> float:
    if k > len(y_true):
        return float("nan")
    true_top = set(np.argsort(y_true)[::-1][:k])
    pred_top = set(np.argsort(y_pred)[::-1][:k])
    return len(true_top & pred_top) / k


def bo_uplift(y_true: np.ndarray, y_pred: np.ndarray, k: int, seed: int) -> float:
    """Mean actual score of the predicted top-k minus mean of random k draws."""
    if k > len(y_true):
        return float("nan")
    pred_top_idx = np.argsort(y_pred)[::-1][:k]
    rng = np.random.default_rng(seed)
    rand_idx = rng.choice(len(y_true), size=k, replace=False)
    return float(y_true[pred_top_idx].mean() - y_true[rand_idx].mean())


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


def run_one_split(
    X_train, y_train, X_test, y_test, seed: int
) -> dict[str, dict[str, float]]:
    """Run all models on a single (target, seed) split and return per-model metrics."""
    out: dict[str, dict[str, float]] = {}
    for name, model in make_models(seed).items():
        pred = fit_predict(model, X_train, y_train, X_test, seed, needs_scaling(name))
        out[name] = evaluate(y_test, pred, seed)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-train", type=int, default=N_TRAIN_DEFAULT)
    parser.add_argument("--emb", default=str(EMB_PATH_320))
    parser.add_argument(
        "--out",
        default=str(RESULTS_DIR / "exp1_model_comparison.parquet"),
    )
    args = parser.parse_args()

    df = pd.read_csv(CSV_PATH)
    embeddings = np.load(args.emb)
    if embeddings.shape[0] != len(df):
        raise SystemExit(
            f"Embedding shape {embeddings.shape} does not match CSV rows {len(df)}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    started = time.time()

    targets = sorted(df["target"].unique())
    print(
        f"Targets: {len(targets)}, Seeds: {len(SEEDS)}, Models: 8, "
        f"Selections: {SELECTION_STRATEGIES}"
    )

    for selection in SELECTION_STRATEGIES:
        for surrogate in SURROGATE_TARGETS:
            for tgt in targets:
                mask = (df["target"] == tgt) & df[surrogate].notna()
                sub = df[mask].reset_index(drop=True)
                indices = df.index[mask].to_numpy()
                X_full = embeddings[indices]
                y_full = sub[surrogate].to_numpy(dtype=np.float64)

                n_avail = len(sub)
                if n_avail < args.n_train + 5:
                    print(f"[skip] {selection}/{surrogate}/{tgt}: only {n_avail} labels")
                    continue

                for seed in SEEDS:
                    tr = select_train_indices(X_full, args.n_train, seed, selection)
                    te = np.setdiff1d(np.arange(n_avail), tr, assume_unique=False)

                    results = run_one_split(
                        X_full[tr], y_full[tr], X_full[te], y_full[te], seed
                    )
                    for model_name, metrics in results.items():
                        base = {
                            "selection": selection,
                            "surrogate": surrogate,
                            "target": tgt,
                            "seed": seed,
                            "n_train": int(len(tr)),
                            "n_test": int(len(te)),
                            "model": model_name,
                        }
                        base.update(metrics)
                        rows.append(base)

                elapsed = time.time() - started
                print(
                    f"  done {selection}/{surrogate}/{tgt} "
                    f"(n_avail={n_avail}, elapsed={elapsed:.0f}s)"
                )

    out_df = pd.DataFrame(rows)
    out_df.to_parquet(args.out, index=False)
    print(f"Saved {args.out} with {len(out_df)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
