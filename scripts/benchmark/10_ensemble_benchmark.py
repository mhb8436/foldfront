#!/usr/bin/env python3
"""
Test whether running multiple surrogate models in parallel and combining their
votes/ranks beats running a single best model.

Three combination rules, all over the four production models that survived
Section-4 statistical equivalence (RF + Ridge + LightGBM + XGBoost):

    score_mean : mean of per-model raw predictions
    rank_mean  : per-model ranks of raw predictions, then mean-aggregated
                 (bounded; insensitive to scale and outliers)
    vote_topk  : each model casts a Top-K binary vote, majority wins
                 (for Top-K = 5: a candidate counts if >= 2 of 4 models pick it)

Compared against:
    each single model (RF / Ridge / LightGBM / XGBoost)
    a uniform random baseline

Metrics: BO uplift Top-5, Top-5 / Top-20 recall on pLDDT and SoluProt;
plus the bias-analysis metrics (overfit_identity, internal_identity) on the
Ensemble Top-10 to see whether ensemble disagreement diversifies the picks.
"""

from __future__ import annotations

import sys
import warnings
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline as SkPipeline
from sklearn.preprocessing import StandardScaler

import lightgbm as lgb
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).parent))
from _selection import select_train_indices

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
RESULTS_DIR = DATA_DIR / "results"

CSV = DATA_DIR / "cath_pilot_dataset.csv"
EMB = DATA_DIR / "cath_pilot_emb_320d.npy"
SEEDS = [42, 123, 7, 2024, 31337]
N_TRAIN = 30
TOP_K = 10
SELECTION = "kmeans"
ENSEMBLE_MODELS = ["RF", "Ridge", "LightGBM", "XGBoost"]


def make_models(seed: int) -> dict[str, object]:
    return {
        "RF": RandomForestRegressor(n_estimators=100, random_state=seed, n_jobs=1),
        "Ridge": SkPipeline([
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=1.0, random_state=seed)),
        ]),
        "LightGBM": lgb.LGBMRegressor(
            n_estimators=100, num_leaves=7, min_data_in_leaf=2, min_data_in_bin=1,
            learning_rate=0.05, random_state=seed, n_jobs=1, verbose=-1,
        ),
        "XGBoost": xgb.XGBRegressor(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            random_state=seed, n_jobs=1, verbosity=0,
        ),
    }


def fit_predict_all(models: dict, X_tr, y_tr, X_te) -> dict[str, np.ndarray]:
    """Fit each model on the same (X_tr, y_tr) and return per-model predictions on X_te."""
    preds: dict[str, np.ndarray] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, m in models.items():
            m.fit(X_tr, y_tr)
            preds[name] = m.predict(X_te)
    return preds


def aggregate_score_mean(preds: dict[str, np.ndarray]) -> np.ndarray:
    return np.mean(np.stack(list(preds.values()), axis=0), axis=0)


def aggregate_rank_mean(preds: dict[str, np.ndarray]) -> np.ndarray:
    """Mean of per-model ranks. Higher rank = predicted-better. Returns a pseudo-score."""
    ranks = []
    for v in preds.values():
        order = np.argsort(np.argsort(v))
        ranks.append(order.astype(np.float64))
    return np.mean(np.stack(ranks, axis=0), axis=0)


def aggregate_vote(preds: dict[str, np.ndarray], k: int) -> np.ndarray:
    """Each model casts a Top-K vote; ties broken by mean rank."""
    n = next(iter(preds.values())).shape[0]
    votes = np.zeros(n, dtype=np.int32)
    rank_mean = aggregate_rank_mean(preds)
    for v in preds.values():
        top = np.argsort(v)[::-1][:k]
        votes[top] += 1
    return votes.astype(np.float64) + rank_mean / (rank_mean.max() + 1.0) * 0.01


def topk_recall(y_true, y_pred, k):
    if k > len(y_true):
        return float("nan")
    return len(set(np.argsort(y_true)[::-1][:k]) & set(np.argsort(y_pred)[::-1][:k])) / k


def bo_uplift(y_true, y_pred, k, seed):
    if k > len(y_true):
        return float("nan")
    pred_top = np.argsort(y_pred)[::-1][:k]
    rng = np.random.default_rng(seed)
    rand = rng.choice(len(y_true), size=k, replace=False)
    return float(y_true[pred_top].mean() - y_true[rand].mean())


def per_residue_identity(a: str, b: str) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(1 for i in range(n) if a[i] == b[i]) / max(len(a), len(b))


def mean_identity(seqs: list[str], anchor: str) -> float:
    if not seqs:
        return float("nan")
    return float(np.mean([per_residue_identity(s, anchor) for s in seqs]))


def mean_pairwise(seqs: list[str]) -> float:
    n = len(seqs)
    if n < 2:
        return float("nan")
    vals = []
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
    for surrogate in ["plddt", "soluprot"]:
        for tgt in targets:
            mask = (df["target"] == tgt) & df[surrogate].notna()
            sub = df[mask].reset_index(drop=True)
            indices = df.index[mask].to_numpy()
            if len(sub) < N_TRAIN + TOP_K + 5:
                continue
            X_full = embeddings[indices]
            y_full = sub[surrogate].to_numpy(dtype=np.float64)
            seqs_full = sub["sequence"].astype(str).tolist()

            for seed in SEEDS:
                rng = np.random.default_rng(seed)
                tr_local = select_train_indices(X_full, N_TRAIN, seed, SELECTION)
                tr_set = set(tr_local.tolist())
                te_local = np.array([i for i in range(len(sub)) if i not in tr_set])
                X_tr, y_tr = X_full[tr_local], y_full[tr_local]
                X_te, y_te = X_full[te_local], y_full[te_local]
                if np.std(y_tr) < 1e-8:
                    continue

                preds = fit_predict_all(make_models(seed), X_tr, y_tr, X_te)
                aggs = {
                    "score_mean": aggregate_score_mean(preds),
                    "rank_mean": aggregate_rank_mean(preds),
                    "vote_top5": aggregate_vote(preds, 5),
                }

                train_best_seq = seqs_full[int(tr_local[int(np.argmax(y_tr))])]
                te_seqs = [seqs_full[int(te_local[i])] for i in range(len(te_local))]

                for combo, score in {**preds, **aggs}.items():
                    base = {
                        "surrogate": surrogate,
                        "target": tgt,
                        "seed": int(seed),
                        "combo": combo,
                        "is_ensemble": combo in aggs,
                        "top5_recall": topk_recall(y_te, score, 5),
                        "top20_recall": topk_recall(y_te, score, 20),
                        "bo_uplift_top5": bo_uplift(y_te, score, 5, seed),
                    }
                    if surrogate == "plddt":
                        top10_idx = np.argsort(score)[::-1][:10]
                        top10_seqs = [te_seqs[i] for i in top10_idx]
                        base["overfit_identity"] = mean_identity(top10_seqs, train_best_seq)
                        base["internal_identity"] = mean_pairwise(top10_seqs)
                    rows.append(base)
            print(f"  done {surrogate}/{tgt}")

    out = pd.DataFrame(rows)
    out_path = RESULTS_DIR / "exp4_ensemble.parquet"
    out.to_parquet(out_path, index=False)
    print(f"Saved {out_path} ({len(out)} rows)")

    print("\n=== BO uplift Top-5 (mean) — pLDDT, K-Means, N=30 ===")
    g = (out[out.surrogate == "plddt"]
         .groupby("combo")["bo_uplift_top5"].mean()
         .sort_values(ascending=False).round(3))
    print(g.to_string())

    print("\n=== BO uplift Top-5 (mean) — SoluProt ===")
    g2 = (out[out.surrogate == "soluprot"]
          .groupby("combo")["bo_uplift_top5"].mean()
          .sort_values(ascending=False).round(4))
    print(g2.to_string())

    print("\n=== Bias on pLDDT Top-10 — overfit / internal identity ===")
    plddt = out[out.surrogate == "plddt"].copy()
    bias = plddt.groupby("combo")[["overfit_identity", "internal_identity"]].mean().round(4)
    print(bias.to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
