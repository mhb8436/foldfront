#!/usr/bin/env python3
"""
Aggregate Exp1 / Exp2 parquet outputs into paper-ready summary tables and
significance reports.

Outputs (under data/benchmark/results/):
    summary_exp1_models.csv       - per-model mean / 95% bootstrap CI per metric
    summary_exp2_n_curve.csv      - per-(N, model) mean / CI
    pairwise_wilcoxon_exp1.csv    - paired Wilcoxon vs RF (Holm-Bonferroni adjusted)
    sample_size_uplift_table.csv  - N_train -> mean BO uplift for the paper

Bootstrap is target-aware: we resample target clusters with replacement, then
average within each bootstrap sample. This respects the per-target correlation
structure that a naive observation-level bootstrap would ignore.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark" / "results"

EXP1 = RESULTS_DIR / "exp1_model_comparison.parquet"
EXP2 = RESULTS_DIR / "exp2_sample_size.parquet"

SURROGATES = ["plddt", "soluprot"]
METRICS = ["spearman", "r2", "mae", "top5_recall", "top20_recall",
           "bo_uplift_top5", "bo_uplift_top20"]
PRIMARY_SELECTION = "kmeans"
N_BOOTSTRAP = 1000
RNG_SEED = 20260427


def cluster_bootstrap_ci(
    df: pd.DataFrame,
    metric: str,
    cluster_col: str = "target",
    n_boot: int = N_BOOTSTRAP,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Mean and 95% CI from a target-clustered bootstrap. Returns (mean, lo, hi).

    Vectorized: pre-aggregate per-cluster means, then resample those scalars.
    """
    per_cluster = df.groupby(cluster_col)[metric].mean().dropna().to_numpy()
    if per_cluster.size == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, per_cluster.size, size=(n_boot, per_cluster.size))
    boots = per_cluster[idx].mean(axis=1)
    return (
        float(per_cluster.mean()),
        float(np.quantile(boots, alpha / 2)),
        float(np.quantile(boots, 1 - alpha / 2)),
    )


def summarize_exp1() -> pd.DataFrame:
    """Per-(selection, surrogate, model, metric) bootstrap summary."""
    df = pd.read_parquet(EXP1)
    selections = sorted(df["selection"].unique()) if "selection" in df.columns else ["random"]
    rows: list[dict] = []
    for selection in selections:
        sel_df = df[df["selection"] == selection] if "selection" in df.columns else df
        for surrogate in SURROGATES:
            sub = sel_df[sel_df.surrogate == surrogate]
            for model in sorted(sub["model"].unique()):
                sub_m = sub[sub["model"] == model]
                for metric in METRICS:
                    clean = sub_m.dropna(subset=[metric])
                    if clean.empty:
                        rows.append(
                            {
                                "selection": selection,
                                "surrogate": surrogate,
                                "model": model,
                                "metric": metric,
                                "mean": float("nan"),
                                "ci_low": float("nan"),
                                "ci_high": float("nan"),
                                "n_obs": 0,
                            }
                        )
                        continue
                    mean, lo, hi = cluster_bootstrap_ci(clean, metric)
                    rows.append(
                        {
                            "selection": selection,
                            "surrogate": surrogate,
                            "model": model,
                            "metric": metric,
                            "mean": mean,
                            "ci_low": lo,
                            "ci_high": hi,
                            "n_obs": int(len(clean)),
                        }
                    )
    out = pd.DataFrame(rows)
    out.to_csv(RESULTS_DIR / "summary_exp1_models.csv", index=False)
    print(f"Saved summary_exp1_models.csv ({len(out)} rows)")
    return out


def pairwise_wilcoxon_exp1() -> pd.DataFrame:
    """Paired Wilcoxon vs RF, per (selection, surrogate, metric)."""
    df = pd.read_parquet(EXP1)
    rows: list[dict] = []
    selections = sorted(df["selection"].unique()) if "selection" in df.columns else ["random"]
    for selection in selections:
        sel_df = df[df["selection"] == selection] if "selection" in df.columns else df
        for surrogate in SURROGATES:
            sub = sel_df[sel_df.surrogate == surrogate]
            rf = sub[sub["model"] == "RF"].set_index(["target", "seed"])
            for model in sorted(sub["model"].unique()):
                if model == "RF":
                    continue
                other = sub[sub["model"] == model].set_index(["target", "seed"])
                for metric in METRICS:
                    pairs = pd.concat(
                        [rf[metric], other[metric]], axis=1, keys=["rf", "other"]
                    ).dropna()
                    if len(pairs) < 5:
                        rows.append(
                            {"selection": selection, "surrogate": surrogate, "metric": metric,
                             "model_vs_RF": model, "n_pairs": int(len(pairs)),
                             "rf_mean": float("nan"), "other_mean": float("nan"),
                             "diff_mean": float("nan"), "p_value": float("nan"),
                             "cliffs_delta": float("nan")}
                        )
                        continue
                    diff = pairs["rf"] - pairs["other"]
                    if diff.abs().sum() < 1e-12:
                        p = 1.0
                    else:
                        try:
                            _, p = stats.wilcoxon(diff, zero_method="wilcox")
                        except ValueError:
                            p = float("nan")
                    a = pairs["rf"].to_numpy()
                    b = pairs["other"].to_numpy()
                    gt = float((a[:, None] > b[None, :]).mean())
                    lt = float((a[:, None] < b[None, :]).mean())
                    rows.append(
                        {
                            "selection": selection,
                            "surrogate": surrogate,
                            "metric": metric,
                            "model_vs_RF": model,
                            "n_pairs": int(len(pairs)),
                            "rf_mean": float(pairs["rf"].mean()),
                            "other_mean": float(pairs["other"].mean()),
                            "diff_mean": float(diff.mean()),
                            "p_value": float(p),
                            "cliffs_delta": gt - lt,
                        }
                    )
    out = pd.DataFrame(rows)

    for (selection, surrogate, metric), grp in out.groupby(["selection", "surrogate", "metric"]):
        ps = grp["p_value"].to_numpy(dtype=float)
        valid = ~np.isnan(ps)
        adj = np.full_like(ps, np.nan, dtype=float)
        if valid.any():
            order = np.argsort(ps[valid])
            ranked = ps[valid][order]
            m = len(ranked)
            scaled = (m - np.arange(m)) * ranked
            holm_sorted = np.maximum.accumulate(np.clip(scaled, 0, 1))
            inverse = np.empty_like(order)
            inverse[order] = np.arange(m)
            adj[valid] = holm_sorted[inverse]
        out.loc[grp.index, "p_holm"] = adj

    out.to_csv(RESULTS_DIR / "pairwise_wilcoxon_exp1.csv", index=False)
    print(f"Saved pairwise_wilcoxon_exp1.csv ({len(out)} rows)")
    return out


def summarize_exp2() -> pd.DataFrame:
    """Per-(selection, surrogate, model, n_train, metric) bootstrap summary."""
    df = pd.read_parquet(EXP2)
    selections = sorted(df["selection"].unique()) if "selection" in df.columns else ["random"]
    rows: list[dict] = []
    for selection in selections:
        sel_df = df[df["selection"] == selection] if "selection" in df.columns else df
        for surrogate in SURROGATES:
            sub = sel_df[sel_df.surrogate == surrogate]
            for model in sorted(sub["model"].unique()):
                for n_train in sorted(sub["n_train"].unique()):
                    sub_mn = sub[(sub["model"] == model) & (sub["n_train"] == n_train)]
                    for metric in ["spearman", "top5_recall", "bo_uplift_top5"]:
                        clean = sub_mn.dropna(subset=[metric])
                        if clean.empty:
                            continue
                        mean, lo, hi = cluster_bootstrap_ci(clean, metric)
                        rows.append(
                            {
                                "selection": selection,
                                "surrogate": surrogate,
                                "model": model,
                                "n_train": int(n_train),
                                "metric": metric,
                                "mean": mean,
                                "ci_low": lo,
                                "ci_high": hi,
                                "n_obs": int(len(clean)),
                            }
                        )
    out = pd.DataFrame(rows)
    out.to_csv(RESULTS_DIR / "summary_exp2_n_curve.csv", index=False)
    print(f"Saved summary_exp2_n_curve.csv ({len(out)} rows)")
    return out


def make_n30_uplift_table() -> pd.DataFrame:
    """For each (selection, surrogate, N): RF BO uplift Top-5 and % of best N."""
    df = pd.read_parquet(EXP2)
    selections = sorted(df["selection"].unique()) if "selection" in df.columns else ["random"]
    rows: list[dict] = []
    for selection in selections:
        sel_df = df[df["selection"] == selection] if "selection" in df.columns else df
        for surrogate in SURROGATES:
            sub = sel_df[(sel_df.surrogate == surrogate) & (sel_df["model"] == "RF")]
            per_n = sub.groupby("n_train")["bo_uplift_top5"].mean()
            max_uplift = per_n.max()
            for n_train, value in per_n.items():
                frac = float(value) / float(max_uplift) if max_uplift > 0 else float("nan")
                rows.append(
                    {
                        "selection": selection,
                        "surrogate": surrogate,
                        "n_train": int(n_train),
                        "rf_bo_uplift_top5": float(value),
                        "fraction_of_best": frac,
                    }
                )
    out = pd.DataFrame(rows)
    out.to_csv(RESULTS_DIR / "sample_size_uplift_table.csv", index=False)
    print(f"Saved sample_size_uplift_table.csv ({len(out)} rows)")
    return out


def selection_comparison_table() -> pd.DataFrame:
    """Random vs K-Means selection: paired comparison per (model, surrogate, metric)."""
    df = pd.read_parquet(EXP1)
    if "selection" not in df.columns or set(df["selection"].unique()) != {"random", "kmeans"}:
        print("[skip] selection comparison: need both random and kmeans in exp1")
        return pd.DataFrame()
    rows: list[dict] = []
    for surrogate in SURROGATES:
        sub = df[df.surrogate == surrogate]
        for model in sorted(sub["model"].unique()):
            sub_m = sub[sub["model"] == model]
            kmeans = sub_m[sub_m["selection"] == "kmeans"].set_index(["target", "seed"])
            random_ = sub_m[sub_m["selection"] == "random"].set_index(["target", "seed"])
            for metric in ["spearman", "top5_recall", "top20_recall",
                           "bo_uplift_top5", "bo_uplift_top20"]:
                pairs = pd.concat(
                    [kmeans[metric], random_[metric]], axis=1, keys=["kmeans", "random"]
                ).dropna()
                if len(pairs) < 5:
                    continue
                diff = pairs["kmeans"] - pairs["random"]
                if diff.abs().sum() < 1e-12:
                    p = 1.0
                else:
                    try:
                        _, p = stats.wilcoxon(diff, zero_method="wilcox")
                    except ValueError:
                        p = float("nan")
                a, b = pairs["kmeans"].to_numpy(), pairs["random"].to_numpy()
                gt = float((a[:, None] > b[None, :]).mean())
                lt = float((a[:, None] < b[None, :]).mean())
                rows.append(
                    {
                        "surrogate": surrogate,
                        "model": model,
                        "metric": metric,
                        "kmeans_mean": float(pairs["kmeans"].mean()),
                        "random_mean": float(pairs["random"].mean()),
                        "diff_mean": float(diff.mean()),
                        "p_value": float(p),
                        "cliffs_delta": gt - lt,
                        "n_pairs": int(len(pairs)),
                    }
                )
    out = pd.DataFrame(rows)
    out.to_csv(RESULTS_DIR / "selection_comparison_kmeans_vs_random.csv", index=False)
    print(f"Saved selection_comparison_kmeans_vs_random.csv ({len(out)} rows)")
    return out


def main() -> int:
    if not EXP1.exists() or not EXP2.exists():
        raise SystemExit("Run 02_ and 03_ scripts first")
    summarize_exp1()
    pairwise_wilcoxon_exp1()
    summarize_exp2()
    make_n30_uplift_table()
    selection_comparison_table()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
