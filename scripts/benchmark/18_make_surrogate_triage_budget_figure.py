#!/usr/bin/env python3
"""Create the manuscript composite figure for surrogate-triage budget results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = PROJECT_ROOT / "data" / "benchmark" / "results"
DEFAULT_FIGURES = PROJECT_ROOT / "figures" / "benchmark"


PALETTE = {
    "teal": "#00796B",
    "blue": "#56B4E9",
    "orange": "#E69F00",
    "purple": "#CC79A7",
    "gray": "#6B7280",
    "light_gray": "#E5E7EB",
    "dark": "#111827",
}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _collect_derived_from_outputs(
    output_root: Path, run_ids: list[str]
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    cv_rows: list[dict[str, object]] = []
    topk_rows: list[dict[str, object]] = []
    wt_rows: list[dict[str, object]] = []
    for run_id in run_ids:
        run_dir = output_root / run_id
        target = run_id.split("_")[-1]
        soluprot_by_tier: dict[str, dict[str, float]] = {}
        tiers_dir = run_dir / "tiers"
        if tiers_dir.exists():
            for sol_path in sorted(tiers_dir.glob("*/soluprot.json")):
                try:
                    payload = json.loads(sol_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                scores = payload.get("scores") if isinstance(payload, dict) else None
                if isinstance(scores, dict):
                    tier_key = sol_path.parent.name
                    soluprot_by_tier[tier_key] = {
                        str(k): float(v)
                        for k, v in scores.items()
                        if isinstance(v, (int, float))
                    }
        cv_path = run_dir / "surrogate_triage" / "cv_metrics.csv"
        if cv_path.exists():
            with cv_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    row = dict(row)
                    row["run_id"] = run_id
                    row["target"] = target
                    cv_rows.append(row)
        topk_path = run_dir / "surrogate_triage" / "acquired_topk.csv"
        if topk_path.exists():
            with topk_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    row = dict(row)
                    row["run_id"] = run_id
                    row["target"] = target
                    tier = str(row.get("tier") or "").strip()
                    seq_id = str(row.get("seq_id") or "").strip()
                    row["soluprot"] = soluprot_by_tier.get(tier, {}).get(seq_id, "")
                    topk_rows.append(row)
        wt_path = run_dir / "wt" / "metrics.json"
        if wt_path.exists():
            try:
                wt_payload = json.loads(wt_path.read_text(encoding="utf-8"))
            except Exception:
                wt_payload = {}
            if isinstance(wt_payload, dict):
                sol = wt_payload.get("soluprot")
                af2 = wt_payload.get("af2")
                sol = sol if isinstance(sol, dict) else {}
                af2 = af2 if isinstance(af2, dict) else {}
                wt_rows.append(
                    {
                        "run_id": run_id,
                        "target": target,
                        "sequence_length": wt_payload.get("sequence_length", ""),
                        "wt_soluprot": sol.get("score", ""),
                        "wt_soluprot_passed": sol.get("passed", ""),
                        "wt_plddt": af2.get("best_plddt", ""),
                        "wt_rmsd_ca": af2.get("rmsd_ca", ""),
                        "provider": af2.get("provider", ""),
                        "source": "run_artifact",
                    }
                )
    return cv_rows, topk_rows, wt_rows


def _prepare_data(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    run_df = _read_csv(Path(args.run_csv))
    if run_df.empty:
        raise SystemExit(f"Missing run summary: {args.run_csv}")
    run_ids = [str(x) for x in run_df["run_id"].tolist()]

    cv_path = Path(args.cv_csv)
    topk_path = Path(args.topk_csv)
    wt_path = Path(args.wt_csv)
    if args.collect_outputs or not cv_path.exists() or not topk_path.exists() or not wt_path.exists():
        cv_rows, topk_rows, wt_rows = _collect_derived_from_outputs(Path(args.output_root), run_ids)
        if cv_rows:
            _write_csv(
                cv_path,
                cv_rows,
                [
                    "run_id",
                    "target",
                    "policy",
                    "status",
                    "selection_score",
                    "spearman",
                    "kendall",
                    "mae",
                    "rmse",
                    "top_quartile_precision",
                    "top_quartile_enrichment",
                    "n_labels",
                    "cv_folds",
                    "error",
                ],
            )
        if topk_rows:
            _write_csv(
                topk_path,
                topk_rows,
                [
                    "run_id",
                    "target",
                    "rank",
                    "global_seq_id",
                    "tier",
                    "seq_id",
                    "acquisition_policy",
                    "acquisition_score",
                    "af2_label",
                    "soluprot",
                    "sequence",
                ],
            )
        if wt_rows:
            _write_csv(
                wt_path,
                wt_rows,
                [
                    "run_id",
                    "target",
                    "sequence_length",
                    "wt_soluprot",
                    "wt_soluprot_passed",
                    "wt_plddt",
                    "wt_rmsd_ca",
                    "provider",
                    "source",
                ],
            )

    cv_df = _read_csv(cv_path)
    topk_df = _read_csv(topk_path)
    wt_df = _read_csv(wt_path)
    return run_df, cv_df, topk_df, wt_df


def _format_int(value: float) -> str:
    return f"{int(round(value)):,}"


def _add_panel_label(ax: plt.Axes, label: str) -> None:
    # Panel labels are added in figure coordinates after layout adjustment.
    return


def make_figure(
    run_df: pd.DataFrame,
    cv_df: pd.DataFrame,
    topk_df: pd.DataFrame,
    wt_df: pd.DataFrame,
    output: Path,
) -> None:
    run_df = run_df.copy()
    run_df["target"] = run_df["target"].astype(str)
    target_order = run_df["target"].tolist()
    total_before = float(run_df["candidate_count_before_triage"].sum())
    total_after = float(run_df["af2_records"].sum())
    reduction = 100.0 * (1.0 - total_after / total_before)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )

    fig = plt.figure(figsize=(7.7, 5.75))
    gs = fig.add_gridspec(2, 2, hspace=0.62, wspace=0.38)

    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

    # Panel A: aggregate AF2/ColabFold call budget.
    bars = ax_a.barh(
        ["Fold all\ntriage candidates", "RAPID\nsurrogate triage"],
        [total_before, total_after],
        color=[PALETTE["light_gray"], PALETTE["teal"]],
        edgecolor=[PALETTE["gray"], PALETTE["dark"]],
        linewidth=0.8,
    )
    ax_a.set_xscale("log")
    ax_a.set_xlabel("AF2/ColabFold evaluations (log scale)")
    ax_a.set_title("AF2/ColabFold calls under fixed triage budget")
    for bar, value in zip(bars, [total_before, total_after]):
        ax_a.text(value * 1.12, bar.get_y() + bar.get_height() / 2, _format_int(value), va="center")
    ax_a.text(
        0.03,
        0.08,
        f"{100.0 - reduction:.1f}% evaluated\nunder fold-all accounting",
        transform=ax_a.transAxes,
        color=PALETTE["teal"],
        fontweight="bold",
    )
    _add_panel_label(ax_a, "A")

    # Panel B: retrospective Top-5 recall (fully labeled 77-target benchmark).
    # Separate from the strict five-target operating benchmark in A/C/D — here the
    # held-out pool is fully labeled, so Top-5 recall is measurable. Values from
    # the surrogate-family comparison (Supplementary Note 4).
    recall_csv = DEFAULT_RESULTS / "summary_exp1_models.csv"
    recall = {}
    if recall_csv.exists():
        mc = pd.read_csv(recall_csv)
        mc = mc[(mc["selection"] == "kmeans") & (mc["metric"] == "top5_recall")]
        for _, r in mc.iterrows():
            recall[(str(r["surrogate"]), str(r["model"]))] = float(r["mean"])
    # Best surrogate per objective + random baseline
    groups = [
        ("pLDDT", "RF",    recall.get(("plddt", "RF"), 0.131),    recall.get(("plddt", "Random"), 0.055)),
        ("SoluProt", "Ridge", recall.get(("soluprot", "Ridge"), 0.703), recall.get(("soluprot", "Random"), 0.051)),
    ]
    xb = np.arange(len(groups)); wb = 0.36
    for j, (obj, surro, sval, rval) in enumerate(groups):
        ax_b.bar(xb[j] - wb/2, sval, wb, color=PALETTE["teal"], edgecolor=PALETTE["dark"],
                 linewidth=0.6, label=("surrogate" if j == 0 else None))
        ax_b.bar(xb[j] + wb/2, rval, wb, color=PALETTE["light_gray"], edgecolor=PALETTE["gray"],
                 linewidth=0.6, label=("random" if j == 0 else None))
        ax_b.text(xb[j] - wb/2, sval + 0.01, f"{surro}\n{sval:.2f}", ha="center", va="bottom", fontsize=6.5, linespacing=0.9)
        ax_b.text(xb[j] + wb/2, rval + 0.01, f"random\n{rval:.2f}", ha="center", va="bottom", fontsize=6.5, linespacing=0.9)
    ax_b.set_xticks(xb)
    ax_b.set_xticklabels([g[0] for g in groups])
    ax_b.set_ylim(0, 0.9)
    ax_b.set_ylabel("Top-5 recall")
    ax_b.set_title("Retrospective Top-5 recall\nin fully labeled CATH pools")
    _add_panel_label(ax_b, "B")

    # Panel C: internal CV ranking signal for fitted policies.
    cv_plot = cv_df.copy()
    cv_plot = cv_plot[cv_plot["status"].astype(str).eq("fitted")]
    cv_plot["target"] = cv_plot["target"].astype(str)
    cv_plot["spearman"] = pd.to_numeric(cv_plot["spearman"], errors="coerce")
    selected = dict(zip(run_df["target"].astype(str), run_df["selected_policy"].astype(str)))
    offsets = {"rf": -0.16, "ridge": 0.16}
    colors = {"rf": PALETTE["teal"], "ridge": PALETTE["orange"]}
    markers = {"rf": "o", "ridge": "s"}
    for policy in ["rf", "ridge"]:
        sub = cv_plot[cv_plot["policy"].astype(str).eq(policy)]
        y_values = []
        x_values = []
        for idx, target in enumerate(target_order):
            val = sub.loc[sub["target"].eq(target), "spearman"]
            if not val.empty:
                x_values.append(idx + offsets[policy])
                y_values.append(float(val.iloc[0]))
        ax_c.scatter(
            x_values,
            y_values,
            s=36,
            marker=markers[policy],
            color=colors[policy],
            edgecolor=PALETTE["dark"],
            linewidth=0.4,
            zorder=3,
        )
    for idx, target in enumerate(target_order):
        chosen = selected.get(target, "")
        if chosen in offsets:
            val = cv_plot.loc[(cv_plot["target"].eq(target)) & (cv_plot["policy"].eq(chosen)), "spearman"]
            if not val.empty:
                ax_c.scatter(
                    [idx + offsets[chosen]],
                    [float(val.iloc[0])],
                    s=92,
                    facecolors="none",
                    edgecolors=PALETTE["dark"],
                    linewidth=1.3,
                    zorder=4,
                )
    ax_c.axhline(0, color=PALETTE["gray"], linewidth=0.8, linestyle="--")
    ax_c.set_xticks(np.arange(len(target_order)))
    ax_c.set_xticklabels(target_order, rotation=35, ha="right")
    ax_c.set_ylabel("CV Spearman on 30 bootstrap labels")
    ax_c.set_title("Policy selection from bootstrap labels")
    ax_c.set_ylim(-0.24, 1.10)
    policy_handles = [
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor=colors["rf"], markeredgecolor=PALETTE["dark"], markersize=5.2, label="RF"),
        Line2D([0], [0], marker="s", linestyle="None", markerfacecolor=colors["ridge"], markeredgecolor=PALETTE["dark"], markersize=5.2, label="Ridge"),
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor="none", markeredgecolor=PALETTE["dark"], markeredgewidth=1.3, markersize=7.8, label="selected policy"),
    ]
    _add_panel_label(ax_c, "C")

    # Panel D: selected candidates relative to the WT baseline.
    topk_plot = topk_df.copy()
    topk_plot["target"] = topk_plot["target"].astype(str)
    topk_plot["af2_label"] = pd.to_numeric(topk_plot["af2_label"], errors="coerce")
    topk_plot["soluprot"] = pd.to_numeric(topk_plot.get("soluprot"), errors="coerce")
    wt_plot = wt_df.copy()
    wt_plot["target"] = wt_plot["target"].astype(str)
    wt_plot["wt_plddt"] = pd.to_numeric(wt_plot["wt_plddt"], errors="coerce")
    wt_plot["wt_soluprot"] = pd.to_numeric(wt_plot["wt_soluprot"], errors="coerce")
    merged_wt = wt_plot.set_index("target")[["wt_plddt", "wt_soluprot"]]
    topk_plot = topk_plot.join(merged_wt, on="target")
    topk_plot = topk_plot.dropna(subset=["af2_label", "soluprot", "wt_plddt", "wt_soluprot"])
    wt_plot = wt_plot[wt_plot["target"].isin(target_order)].dropna(subset=["wt_plddt", "wt_soluprot"])
    candidate_handles: list[Line2D] = []
    candidate_summary = "WT comparison unavailable."
    if topk_plot.empty or wt_plot.empty:
        ax_d.text(0.5, 0.5, "WT comparison unavailable", ha="center", va="center")
        ax_d.set_axis_off()
    else:
        topk_plot["delta_plddt"] = topk_plot["af2_label"] - topk_plot["wt_plddt"]
        topk_plot["delta_soluprot"] = topk_plot["soluprot"] - topk_plot["wt_soluprot"]
        improves_both = (topk_plot["delta_plddt"] > 0) & (topk_plot["delta_soluprot"] > 0)
        other = topk_plot.loc[~improves_both]
        improved = topk_plot.loc[improves_both]
        if not other.empty:
            ax_d.scatter(
                other["delta_plddt"],
                other["delta_soluprot"],
                s=17,
                color=PALETTE["light_gray"],
                alpha=0.9,
                edgecolor="white",
                linewidth=0.25,
                label="Selected Top-K",
                zorder=2,
            )
        if not improved.empty:
            ax_d.scatter(
                improved["delta_plddt"],
                improved["delta_soluprot"],
                s=19,
                color=PALETTE["teal"],
                alpha=0.68,
                edgecolor="white",
                linewidth=0.25,
                label="Above WT both",
                zorder=3,
            )
        ax_d.scatter(
            [0],
            [0],
            s=86,
            marker="D",
            color=PALETTE["blue"],
            edgecolor=PALETTE["dark"],
            linewidth=0.8,
            label="WT",
            zorder=4,
        )
        improved_both = 0
        for target in target_order:
            sub = topk_plot[topk_plot["target"].eq(target)]
            if sub.empty:
                continue
            if ((sub["delta_plddt"] > 0) & (sub["delta_soluprot"] > 0)).any():
                improved_both += 1
        ax_d.axvline(0, color=PALETTE["gray"], linewidth=0.8, linestyle="--", zorder=1)
        ax_d.axhline(0, color=PALETTE["gray"], linewidth=0.8, linestyle="--", zorder=1)
        ax_d.set_xlabel("Delta pLDDT vs target-matched WT")
        ax_d.set_ylabel("Delta SoluProt vs target-matched WT")
        ax_d.set_title("Selected Top-K relative to WT")
        candidate_handles = [
            Line2D([0], [0], marker="o", linestyle="None", markerfacecolor=PALETTE["light_gray"], markeredgecolor="white", markersize=5.0, label="Selected Top-K"),
            Line2D([0], [0], marker="o", linestyle="None", markerfacecolor=PALETTE["teal"], markeredgecolor="white", markersize=5.0, label="Above WT both"),
            Line2D([0], [0], marker="D", linestyle="None", markerfacecolor=PALETTE["blue"], markeredgecolor=PALETTE["dark"], markersize=6.2, label="WT"),
        ]
        candidate_summary = (
            f"{int(improves_both.sum())}/{len(topk_plot)} selected candidates and "
            f"{improved_both}/{len(target_order)} targets are above WT on both proxies."
        )
    _add_panel_label(ax_d, "D")

    fig.suptitle("Fixed-budget surrogate triage under fold-all accounting", y=0.965, fontsize=10.8, fontweight="bold")
    fig.subplots_adjust(left=0.10, right=0.98, top=0.88, bottom=0.19)

    for label, ax in [("A", ax_a), ("B", ax_b), ("C", ax_c), ("D", ax_d)]:
        pos = ax.get_position()
        fig.text(
            pos.x0 - 0.040,
            pos.y1 + 0.010,
            label,
            fontsize=11,
            fontweight="bold",
            ha="left",
            va="bottom",
        )

    c_pos = ax_c.get_position()
    fig.legend(
        handles=policy_handles,
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(c_pos.x0 + c_pos.width / 2, c_pos.y0 - 0.055),
        ncol=3,
        handlelength=1.0,
        handletextpad=0.35,
        columnspacing=0.8,
    )
    if candidate_handles:
        d_pos = ax_d.get_position()
        fig.legend(
            handles=candidate_handles,
            frameon=False,
            loc="upper center",
            bbox_to_anchor=(d_pos.x0 + d_pos.width / 2, d_pos.y0 - 0.055),
            ncol=3,
            handlelength=1.0,
            handletextpad=0.35,
            columnspacing=0.8,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=350, bbox_inches="tight")
    fig.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-csv", default=str(DEFAULT_RESULTS / "surrogate_triage_budget_run_summary.csv"))
    parser.add_argument("--cv-csv", default=str(DEFAULT_RESULTS / "surrogate_triage_cv_metrics.csv"))
    parser.add_argument("--topk-csv", default=str(DEFAULT_RESULTS / "surrogate_triage_acquired_topk.csv"))
    parser.add_argument("--wt-csv", default=str(DEFAULT_RESULTS / "surrogate_triage_wt_metrics.csv"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--output", default=str(DEFAULT_FIGURES / "fig2_surrogate_triage_budget.png"))
    parser.add_argument("--collect-outputs", action="store_true", help="Regenerate derived CV and Top-K CSVs from run output folders.")
    args = parser.parse_args()

    run_df, cv_df, topk_df, wt_df = _prepare_data(args)
    if cv_df.empty:
        raise SystemExit("No CV metrics found; run with --collect-outputs in the full workspace first.")
    if topk_df.empty:
        raise SystemExit("No acquired Top-K data found; run with --collect-outputs in the full workspace first.")
    make_figure(run_df, cv_df, topk_df, wt_df, Path(args.output))
    print(str(Path(args.output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
