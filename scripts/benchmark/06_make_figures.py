#!/usr/bin/env python3
"""
Generate paper-ready figures and tables from the aggregated CSVs.

Outputs (under figures/benchmark/):
    fig3_selection_n_curves.png  - RF BO uplift vs N under random and K-means selection
    fig4_selection_comparison.png - Per-model effect of selection strategy at fixed N=30
    fig5_model_comparison.png    - Exp1: 8 models x (Spearman + BO uplift), 2-panel per surrogate
    fig7_per_target_heatmap.png  - per-target RF-relative wins
    fig8_sample_size.png         - Exp2: N learning curves, 2-panel per surrogate
    fig9_esm_size.png            - ESM-2 8M (320-D) vs 150M (640-D) embedding ablation
    table2_model_comparison.tex  - LaTeX-ready table for paper section 4
    table3_sample_size.tex       - LaTeX-ready N ablation table

The script is idempotent. Re-running overwrites all artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark" / "results"
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"
CSV_PATH = PROJECT_ROOT / "data" / "benchmark" / "cath_pilot_dataset.csv"

EXP1 = RESULTS_DIR / "exp1_model_comparison.parquet"
EXP2 = RESULTS_DIR / "exp2_sample_size.parquet"
EXP3 = RESULTS_DIR / "exp3_esm_size.parquet"
SUMMARY1 = RESULTS_DIR / "summary_exp1_models.csv"
SUMMARY2 = RESULTS_DIR / "summary_exp2_n_curve.csv"
WILCOXON = RESULTS_DIR / "pairwise_wilcoxon_exp1.csv"
UPLIFT_TBL = RESULTS_DIR / "sample_size_uplift_table.csv"
SELECTION_CMP = RESULTS_DIR / "selection_comparison_kmeans_vs_random.csv"
PRIMARY_SELECTION = "kmeans"

MODEL_ORDER = ["RF", "Ridge", "GP-RBF", "XGBoost", "LightGBM", "KNN", "MLP", "Random"]
MODEL_COLORS = {
    "RF": "#1f77b4",
    "Ridge": "#ff7f0e",
    "GP-RBF": "#2ca02c",
    "XGBoost": "#d62728",
    "LightGBM": "#9467bd",
    "KNN": "#8c564b",
    "MLP": "#e377c2",
    "Random": "#7f7f7f",
}
SURROGATE_LABEL = {"plddt": "pLDDT (ColabFold)", "soluprot": "SoluProt"}


def benchmark_scope_label() -> str:
    """Return a short label that keeps figure titles synchronized with the CSV."""
    try:
        n_targets = pd.read_csv(CSV_PATH, usecols=["target"])["target"].nunique()
    except Exception:
        return "5 seeds x benchmark targets"
    return f"5 seeds x {n_targets} targets"


def setup_style() -> None:
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    plt.rcParams["figure.dpi"] = 130
    plt.rcParams["savefig.dpi"] = 220
    plt.rcParams["axes.titlesize"] = 11
    plt.rcParams["axes.labelsize"] = 10


def fig2_model_comparison() -> None:
    """8 models × 2 metrics × 2 surrogates, K-Means selection (matches evolution.py)."""
    summary = pd.read_csv(SUMMARY1)
    summary = summary[summary["selection"] == PRIMARY_SELECTION]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))

    for col, surrogate in enumerate(["plddt", "soluprot"]):
        for row, metric in enumerate(["spearman", "bo_uplift_top5"]):
            ax = axes[row, col]
            sub = summary[(summary["surrogate"] == surrogate) & (summary["metric"] == metric)].copy()
            sub["model"] = pd.Categorical(sub["model"], categories=MODEL_ORDER, ordered=True)
            sub = sub.sort_values("model")

            x = np.arange(len(sub))
            means = sub["mean"].to_numpy()
            err_low = means - sub["ci_low"].to_numpy()
            err_high = sub["ci_high"].to_numpy() - means
            colors = [MODEL_COLORS.get(m, "#333") for m in sub["model"]]

            ax.bar(x, means, yerr=[err_low, err_high], color=colors, edgecolor="black",
                   linewidth=0.6, capsize=4)
            ax.axhline(0, color="black", linewidth=0.6)
            ax.set_xticks(x)
            ax.set_xticklabels(sub["model"], rotation=20, ha="right")

            metric_label = "Spearman ρ" if metric == "spearman" else "BO uplift (Top-5)"
            ax.set_ylabel(metric_label)
            if row == 0:
                ax.set_title(f"{SURROGATE_LABEL[surrogate]}")

    fig.suptitle(
        "Surrogate model comparison "
        f"(K-Means selection, N=30 train, held-out pool, {benchmark_scope_label()})",
        fontsize=12, y=1.00,
    )
    fig.tight_layout()
    out = FIG_DIR / "fig5_model_comparison.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def fig2b_selection_comparison() -> None:
    """K-Means vs Random selection effect on each model's BO uplift Top-5."""
    if not SELECTION_CMP.exists():
        print(f"[skip] {SELECTION_CMP} missing")
        return
    df = pd.read_csv(SELECTION_CMP)
    df = df[df["metric"] == "bo_uplift_top5"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    for ax, surrogate in zip(axes, ["plddt", "soluprot"]):
        sub = df[df["surrogate"] == surrogate].copy()
        sub["model"] = pd.Categorical(sub["model"], categories=MODEL_ORDER, ordered=True)
        sub = sub.sort_values("model")
        x = np.arange(len(sub))
        width = 0.35
        ax.bar(x - width / 2, sub["random_mean"], width, label="Random",
               color="#bbbbbb", edgecolor="black", linewidth=0.6)
        ax.bar(x + width / 2, sub["kmeans_mean"], width, label="K-Means",
               color="#1f77b4", edgecolor="black", linewidth=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(sub["model"], rotation=20, ha="right")
        ax.set_ylabel("BO uplift (Top-5)")
        ax.set_title(SURROGATE_LABEL[surrogate])
        ax.axhline(0, color="black", linewidth=0.6)

    axes[0].legend(loc="upper right", fontsize=9)
    fig.suptitle(
        "Random vs K-Means training-set selection (N=30, BO uplift Top-5)",
        fontsize=12, y=1.02,
    )
    fig.tight_layout()
    out = FIG_DIR / "fig4_selection_comparison.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def fig3_sample_size() -> None:
    """N ablation under K-Means selection (production scenario)."""
    summary = pd.read_csv(SUMMARY2)
    summary = summary[summary["selection"] == PRIMARY_SELECTION]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)

    for col, surrogate in enumerate(["plddt", "soluprot"]):
        for row, metric in enumerate(["spearman", "bo_uplift_top5"]):
            ax = axes[row, col]
            sub = summary[(summary["surrogate"] == surrogate) & (summary["metric"] == metric)]
            for model in sorted(sub["model"].unique()):
                sub_m = sub[sub["model"] == model].sort_values("n_train")
                color = MODEL_COLORS.get(model, "#333")
                ax.plot(sub_m["n_train"], sub_m["mean"], marker="o", color=color,
                        linewidth=2, label=model)
                ax.fill_between(sub_m["n_train"], sub_m["ci_low"], sub_m["ci_high"],
                                color=color, alpha=0.18)
            ax.axvline(30, color="red", linestyle="--", linewidth=1, alpha=0.6)
            ax.text(30, ax.get_ylim()[1], "  N=30 (default)", color="red",
                    fontsize=8, va="top")
            ax.set_xlabel("N_train")
            metric_label = "Spearman ρ" if metric == "spearman" else "BO uplift (Top-5)"
            ax.set_ylabel(metric_label)
            if row == 0:
                ax.set_title(SURROGATE_LABEL[surrogate])
            if row == 0 and col == 1:
                ax.legend(loc="lower right", fontsize=8, framealpha=0.85)

    fig.suptitle(
        "Sample-size ablation "
        f"(K-Means selection, RF + 3 alternatives, 20-sample held-out, {benchmark_scope_label()})",
        fontsize=12, y=1.00,
    )
    fig.tight_layout()
    out = FIG_DIR / "fig8_sample_size.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def fig3b_selection_n_curves() -> None:
    """RF only: Random vs K-Means N curves to show K-Means dominates at low N."""
    summary = pd.read_csv(SUMMARY2)
    sub_rf = summary[(summary["model"] == "RF") & (summary["metric"] == "bo_uplift_top5")]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=False)

    for ax, surrogate in zip(axes, ["plddt", "soluprot"]):
        for selection, color, marker in [("kmeans", "#1f77b4", "o"),
                                         ("random", "#888888", "s")]:
            sub = sub_rf[(sub_rf["surrogate"] == surrogate) & (sub_rf["selection"] == selection)]
            sub = sub.sort_values("n_train")
            ax.plot(sub["n_train"], sub["mean"], marker=marker, color=color,
                    linewidth=2, label=f"RF + {selection}")
            ax.fill_between(sub["n_train"], sub["ci_low"], sub["ci_high"],
                            color=color, alpha=0.18)
        ax.axvline(30, color="red", linestyle="--", linewidth=1, alpha=0.6)
        ax.set_xlabel("N_train")
        ax.set_ylabel("BO uplift (Top-5)")
        ax.set_title(SURROGATE_LABEL[surrogate])
        ax.legend(loc="lower right", fontsize=9)

    fig.suptitle(
        "RF training-set selection: K-Means vs Random across N",
        fontsize=12, y=1.02,
    )
    fig.tight_layout()
    out = FIG_DIR / "fig3_selection_n_curves.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def fig5_per_target_heatmap() -> None:
    """Per-target BO uplift Top-5 difference vs RF, K-Means selection only."""
    df = pd.read_parquet(EXP1)
    if "selection" in df.columns:
        df = df[df["selection"] == PRIMARY_SELECTION]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, surrogate in zip(axes, ["plddt", "soluprot"]):
        sub = df[df.surrogate == surrogate]
        rf = sub[sub["model"] == "RF"].groupby("target")["bo_uplift_top5"].mean()
        pivot = sub.groupby(["target", "model"])["bo_uplift_top5"].mean().unstack("model")
        pivot = pivot.subtract(rf, axis=0)
        pivot = pivot.drop(columns=["RF"], errors="ignore").reindex(
            columns=[m for m in MODEL_ORDER if m != "RF"]
        )
        sns.heatmap(
            pivot,
            annot=True,
            fmt=".2f" if surrogate == "plddt" else ".3f",
            center=0,
            cmap="RdBu_r",
            ax=ax,
            cbar_kws={"label": "BO uplift diff vs RF"},
        )
        ax.set_title(SURROGATE_LABEL[surrogate])
        ax.set_ylabel("CATH target")
        ax.set_xlabel("model")

    fig.suptitle("Per-target BO uplift relative to RF (positive = model beats RF)",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    out = FIG_DIR / "fig7_per_target_heatmap.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def table2_model_comparison() -> None:
    """LaTeX table for paper main text: model x metric x surrogate at K-Means selection."""
    summary = pd.read_csv(SUMMARY1)
    wilcoxon = pd.read_csv(WILCOXON)
    if "selection" in summary.columns:
        summary = summary[summary["selection"] == PRIMARY_SELECTION]
    if "selection" in wilcoxon.columns:
        wilcoxon = wilcoxon[wilcoxon["selection"] == PRIMARY_SELECTION]
    metric_set = ["spearman", "top5_recall", "bo_uplift_top5"]
    metric_label = {
        "spearman": "Spearman $\\rho$",
        "top5_recall": "Top-5 recall",
        "bo_uplift_top5": "BO uplift (Top-5)",
    }

    lines: list[str] = []
    for surrogate in ["plddt", "soluprot"]:
        lines.append(f"% --- {surrogate} ---")
        lines.append("\\begin{tabular}{l" + "c" * len(metric_set) + "}")
        lines.append("\\toprule")
        lines.append("Model & " + " & ".join(metric_label[m] for m in metric_set) + " \\\\")
        lines.append("\\midrule")
        sub = summary[summary["surrogate"] == surrogate]
        wsub = wilcoxon[(wilcoxon["surrogate"] == surrogate)]
        for model in MODEL_ORDER:
            cells = [model]
            for metric in metric_set:
                row = sub[(sub["model"] == model) & (sub["metric"] == metric)]
                if row.empty:
                    cells.append("--")
                    continue
                m = row["mean"].iloc[0]
                lo = row["ci_low"].iloc[0]
                hi = row["ci_high"].iloc[0]
                cell = f"{m:.3f} [{lo:.3f}, {hi:.3f}]"
                if model != "RF":
                    wrow = wsub[(wsub["model_vs_RF"] == model) & (wsub["metric"] == metric)]
                    if not wrow.empty:
                        p_holm = wrow["p_holm"].iloc[0]
                        if pd.notna(p_holm) and p_holm < 0.05:
                            cell = "$" + cell + "^{*}$"
                cells.append(cell)
            lines.append(" & ".join(cells) + " \\\\")
        lines.append("\\bottomrule")
        lines.append("\\end{tabular}")
        lines.append("")

    out = FIG_DIR / "table2_model_comparison.tex"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {out}")


def table3_sample_size() -> None:
    """LaTeX N-ablation table for RF, both K-Means and Random for completeness."""
    df = pd.read_csv(UPLIFT_TBL)
    if "selection" in df.columns:
        df = df[df["selection"] == PRIMARY_SELECTION]
    lines: list[str] = []
    lines.append("\\begin{tabular}{lrrrr}")
    lines.append("\\toprule")
    lines.append("$N_{train}$ & pLDDT BO uplift & \\% of N=80 & SoluProt BO uplift & \\% of N=80 \\\\")
    lines.append("\\midrule")
    for n in sorted(df["n_train"].unique()):
        plddt = df[(df["n_train"] == n) & (df["surrogate"] == "plddt")]
        solu = df[(df["n_train"] == n) & (df["surrogate"] == "soluprot")]
        if plddt.empty or solu.empty:
            continue
        p_val = plddt["rf_bo_uplift_top5"].iloc[0]
        p_frac = plddt["fraction_of_best"].iloc[0] * 100
        s_val = solu["rf_bo_uplift_top5"].iloc[0]
        s_frac = solu["fraction_of_best"].iloc[0] * 100
        marker = " (default)" if n == 30 else ""
        lines.append(
            f"{int(n)}{marker} & {p_val:.3f} & {p_frac:.1f} & {s_val:.4f} & {s_frac:.1f} \\\\"
        )
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    out = FIG_DIR / "table3_sample_size.tex"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {out}")


def fig4_esm_size() -> None:
    """Supplementary: ESM-2 8M (320D) vs 150M (640D) bar plot per surrogate."""
    if not EXP3.exists():
        print(f"[skip] {EXP3} missing - run 04_esm_size_ablation.py first")
        return
    df = pd.read_parquet(EXP3)
    current_targets = pd.read_csv(CSV_PATH, usecols=["target"])["target"].nunique()
    if df["target"].nunique() != current_targets or df["embedding"].nunique() < 2:
        print(
            "[skip] ESM-size figure: exp3 does not match the active benchmark "
            f"({df['target'].nunique()} vs {current_targets} targets, "
            f"{df['embedding'].nunique()} embedding size(s))"
        )
        return
    metrics = ["spearman", "top5_recall", "bo_uplift_top5"]
    metric_label = {
        "spearman": "Spearman ρ",
        "top5_recall": "Top-5 recall",
        "bo_uplift_top5": "BO uplift (Top-5)",
    }
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))

    for ax, metric in zip(axes, metrics):
        rows: list[dict] = []
        for surrogate in ["plddt", "soluprot"]:
            for emb in df["embedding"].unique():
                vals = df[(df.surrogate == surrogate) & (df.embedding == emb)][metric].dropna()
                if vals.empty:
                    continue
                per_target = (
                    df[(df.surrogate == surrogate) & (df.embedding == emb)]
                    .groupby("target")[metric]
                    .mean()
                    .to_numpy()
                )
                rng = np.random.default_rng(20260427)
                idx = rng.integers(0, per_target.size, size=(1000, per_target.size))
                boots = per_target[idx].mean(axis=1)
                rows.append(
                    {
                        "surrogate": surrogate,
                        "embedding": emb,
                        "mean": float(per_target.mean()),
                        "ci_low": float(np.quantile(boots, 0.025)),
                        "ci_high": float(np.quantile(boots, 0.975)),
                    }
                )
        sub = pd.DataFrame(rows)
        x = np.arange(2)
        width = 0.35
        for i, emb in enumerate(sorted(df["embedding"].unique())):
            mask = sub["embedding"] == emb
            means = sub.loc[mask, "mean"].to_numpy()
            err_low = means - sub.loc[mask, "ci_low"].to_numpy()
            err_high = sub.loc[mask, "ci_high"].to_numpy() - means
            ax.bar(x + (i - 0.5) * width, means, width,
                   yerr=[err_low, err_high], capsize=4,
                   label=emb, edgecolor="black", linewidth=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels([SURROGATE_LABEL[s] for s in ["plddt", "soluprot"]])
        ax.set_ylabel(metric_label[metric])
        ax.set_title(metric_label[metric])
        ax.axhline(0, color="black", linewidth=0.6)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle(f"ESM-2 embedding size ablation (RF, N=30, {benchmark_scope_label()})",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    out = FIG_DIR / "fig9_esm_size.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


def main() -> int:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    setup_style()
    fig2_model_comparison()
    fig2b_selection_comparison()
    fig3_sample_size()
    fig3b_selection_n_curves()
    fig4_esm_size()
    fig5_per_target_heatmap()
    table2_model_comparison()
    table3_sample_size()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
