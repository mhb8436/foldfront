#!/usr/bin/env python3
"""
Decompose pLDDT variance to separate target-intrinsic difficulty from
ProteinMPNN sampling noise.

Methodology:
    Total variance       = Var(plddt across all benchmark designs)
    Between-target var   = Var of per-target means (target-intrinsic difficulty)
    Within-target var    = Mean of per-target Var (ProteinMPNN sampling noise)
    ICC1 (one-way ANOVA) = sigma^2_between / (sigma^2_between + sigma^2_within)

Interpretation:
    ICC1 ~ 1.0 -> bad sequences are explained by target hardness (intrinsic)
    ICC1 ~ 0.0 -> bad sequences are MPNN sampling noise (model improvable)

Per-target output also includes:
    n_designs, mean, std, min, max, frac_high (>=70 pLDDT, "fold success")
    range = max - min  (MPNN headroom: how much can MPNN help if perfect?)

Outputs:
    data/benchmark/results/mpnn_decomposition_per_target.csv
    data/benchmark/results/mpnn_decomposition_summary.json
    figures/benchmark/fig10_mpnn_decomposition.png
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
RESULTS_DIR = DATA_DIR / "results"
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"
CSV_PATH = DATA_DIR / "cath_pilot_dataset.csv"


def variance_decomposition(values: np.ndarray, group_id: np.ndarray) -> dict:
    """One-way ANOVA-style decomposition with ICC1 (cluster homogeneity)."""
    df = pd.DataFrame({"y": values, "g": group_id}).dropna()
    overall_mean = df["y"].mean()
    grand_var = df["y"].var(ddof=0)

    grp = df.groupby("g")["y"]
    n_per = grp.size().to_numpy(dtype=float)
    means = grp.mean().to_numpy()
    vars_ = grp.var(ddof=0).to_numpy()
    k = len(means)
    n_avg = n_per.mean()
    ss_between = np.sum(n_per * (means - overall_mean) ** 2)
    ss_within = np.sum(n_per * vars_)
    ms_between = ss_between / max(k - 1, 1)
    ms_within = ss_within / max((n_per.sum() - k), 1)

    icc_numerator = ms_between - ms_within
    icc_denominator = ms_between + (n_avg - 1) * ms_within
    icc1 = float(icc_numerator / icc_denominator) if icc_denominator > 0 else float("nan")
    icc1 = max(min(icc1, 1.0), 0.0)

    return {
        "n_total": int(len(df)),
        "n_groups": int(k),
        "grand_mean": float(overall_mean),
        "grand_var": float(grand_var),
        "between_target_var": float(np.var(means, ddof=0)),
        "mean_within_target_var": float(np.mean(vars_)),
        "ms_between": float(ms_between),
        "ms_within": float(ms_within),
        "icc1": icc1,
    }


def per_target_table(df: pd.DataFrame, score_col: str, threshold: float) -> pd.DataFrame:
    rows: list[dict] = []
    for tgt, sub in df.groupby("target"):
        clean = sub[score_col].dropna().to_numpy()
        if clean.size == 0:
            continue
        rows.append(
            {
                "target": tgt,
                "metric": score_col,
                "n_designs": int(clean.size),
                "mean": float(clean.mean()),
                "std": float(clean.std(ddof=1) if clean.size > 1 else 0.0),
                "min": float(clean.min()),
                "max": float(clean.max()),
                "median": float(np.median(clean)),
                "p25": float(np.percentile(clean, 25)),
                "p75": float(np.percentile(clean, 75)),
                "range": float(clean.max() - clean.min()),
                "frac_above_threshold": float(np.mean(clean >= threshold)),
                "threshold": threshold,
            }
        )
    return pd.DataFrame(rows)


def make_figure(df: pd.DataFrame, summary: dict) -> None:
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    fig = plt.figure(figsize=(13, 9))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.2, 1])

    ax_strip = fig.add_subplot(gs[0, :])
    sub = df.dropna(subset=["plddt"])
    target_order = (
        sub.groupby("target")["plddt"].mean().sort_values().index.tolist()
    )
    sns.stripplot(
        data=sub,
        x="target",
        y="plddt",
        order=target_order,
        ax=ax_strip,
        size=2.5,
        alpha=0.55,
        color="#3a76b5",
        jitter=0.32,
    )
    medians = sub.groupby("target")["plddt"].median().reindex(target_order).to_numpy()
    ax_strip.plot(np.arange(len(target_order)), medians, marker="D",
                  color="#d62728", linestyle="-", linewidth=1.4, markersize=6,
                  label="per-target median")
    ax_strip.axhline(70, color="#888", linestyle="--", linewidth=1,
                     label="pLDDT = 70 (fold success)")
    ax_strip.set_title("Per-target pLDDT distributions across ProteinMPNN designs")
    ax_strip.set_xlabel("CATH target (sorted by mean pLDDT)")
    ax_strip.set_ylabel("AlphaFold2 mean pLDDT")
    ax_strip.tick_params(axis="x", rotation=45)
    ax_strip.legend(loc="lower right", fontsize=9)

    ax_metric = fig.add_subplot(gs[1, 0])
    icc = summary["plddt"]["icc1"]
    between = summary["plddt"]["between_target_var"]
    within = summary["plddt"]["mean_within_target_var"]
    bars = ax_metric.bar(
        ["Between-target\n(intrinsic)", "Within-target\n(MPNN noise)"],
        [between, within],
        color=["#1f77b4", "#ff7f0e"],
        edgecolor="black",
    )
    ax_metric.set_ylabel("Variance of pLDDT")
    ax_metric.set_title(
        f"Variance decomposition (ICC1 = {icc:.2f})"
    )
    for bar, val in zip(bars, [between, within]):
        ax_metric.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                       f"{val:.1f}", ha="center", va="bottom", fontsize=9)

    ax_scatter = fig.add_subplot(gs[1, 1])
    per_target = sub.groupby("target")["plddt"].agg(["mean", "max", "std"])
    sns.scatterplot(
        data=per_target.reset_index(),
        x="mean",
        y="std",
        size="max",
        sizes=(40, 220),
        hue="max",
        palette="viridis",
        ax=ax_scatter,
        legend="brief",
    )
    ax_scatter.axhline(per_target["std"].median(), color="grey",
                       linestyle=":", linewidth=1)
    ax_scatter.set_title("Mean vs std per target (size/color = max pLDDT)")
    ax_scatter.set_xlabel("Mean pLDDT")
    ax_scatter.set_ylabel("Std pLDDT (within-target)")

    fig.suptitle(
        "MPNN error decomposition: target-intrinsic difficulty vs sampling noise",
        fontsize=12, y=1.00,
    )
    fig.tight_layout()
    out = FIG_DIR / "fig10_mpnn_decomposition.png"
    fig.savefig(out, bbox_inches="tight", dpi=220)
    plt.close(fig)
    print(f"Saved {out}")


def main() -> int:
    df = pd.read_csv(CSV_PATH)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    plddt_decomp = variance_decomposition(df["plddt"].to_numpy(), df["target"].to_numpy())
    solu_decomp = variance_decomposition(df["soluprot"].to_numpy(), df["target"].to_numpy())

    summary = {"plddt": plddt_decomp, "soluprot": solu_decomp}
    (RESULTS_DIR / "mpnn_decomposition_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    per_target_plddt = per_target_table(df, "plddt", threshold=70.0)
    per_target_solu = per_target_table(df, "soluprot", threshold=0.5)
    per_target = pd.concat([per_target_plddt, per_target_solu], ignore_index=True)
    per_target.to_csv(RESULTS_DIR / "mpnn_decomposition_per_target.csv", index=False)

    print("=== Variance decomposition ===")
    for k, v in summary.items():
        print(f"\n[{k}]")
        for kk, vv in v.items():
            print(f"  {kk:30s} = {vv}")
    print(f"\nSaved per-target table ({len(per_target)} rows)")

    make_figure(df, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
