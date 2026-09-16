#!/usr/bin/env python3
"""Make the QC-filtered CATH benchmark-corpus figure and summary table."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"


def _first_existing(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError("none of the expected CATH curated inputs exists")


def _input_paths() -> tuple[Path, Path]:
    data_dir = _first_existing(
        [
            PROJECT_ROOT / "data" / "cath_curated",
            PROJECT_ROOT / "public_data" / "cath_curated",
            PROJECT_ROOT / "cath_outputs" / "paper_curated",
        ]
    )
    return data_dir / "curated_per_target_summary.csv", data_dir / "curated_summary.json"


def _figure_title(summary: dict) -> str:
    return (
        f"QC-filtered CATH benchmark corpus: "
        f"{int(summary['n_included_runs']):,} targets, "
        f"{int(summary['n_design_rows']):,} valid paired designs"
    )


def _write_summary_table(summary: dict, out_path: Path) -> None:
    rows = [
        ("Completed CATH runs parsed", f"{int(summary['n_total_completed_dirs']):,}"),
        ("QC-included targets", f"{int(summary['n_included_runs']):,}"),
        ("QC-excluded runs", f"{int(summary['n_excluded_runs']):,}"),
        ("Valid paired design rows", f"{int(summary['n_design_rows']):,}"),
        ("Positive pLDDT records", f"{int(summary['n_positive_plddt']):,}"),
        ("SoluProt records", f"{int(summary['n_soluprot']):,}"),
        ("Mean pLDDT", f"{float(summary['mean_plddt']):.2f}"),
        ("Maximum pLDDT", f"{float(summary['max_plddt']):.2f}"),
        ("Mean SoluProt", f"{float(summary['mean_soluprot']):.3f}"),
        ("Maximum SoluProt", f"{float(summary['max_soluprot']):.3f}"),
    ]
    lines = [
        "\\begin{tabular}{lr}",
        "\\toprule",
        "Metric & Value \\\\",
        "\\midrule",
    ]
    lines.extend(f"{metric} & {value} \\\\" for metric, value in rows)
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    per_target_path, summary_path = _input_paths()
    df = pd.read_csv(per_target_path).sort_values("mean_plddt", ascending=True)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    FIG_DIR.mkdir(parents=True, exist_ok=True)

    targets = df["target"].tolist()
    y = range(len(targets))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 7,
        }
    )
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(8.8, 6.0),
        sharey=True,
        gridspec_kw={"width_ratios": [1.15, 1.0], "wspace": 0.08},
    )

    plddt_colors = ["#c84b31" if value < 85 else "#2f6f9f" for value in df["mean_plddt"]]
    ax1.barh(list(y), df["mean_plddt"], color=plddt_colors, height=0.68)
    ax1.axvspan(85, 100, color="#e9f3ea", zorder=-1)
    ax1.axvline(85, color="#4b7f52", linewidth=1.0, linestyle="--")
    ax1.set_xlim(55, 100)
    ax1.set_xlabel("Mean pLDDT")
    ax1.set_yticks(list(y), targets)
    ax1.set_title("Structural confidence", fontsize=10, weight="bold")

    sol_colors = ["#8a3ffc" if value < 0.5 else "#3a7d44" for value in df["mean_soluprot"]]
    ax2.scatter(df["mean_soluprot"], list(y), s=32, color=sol_colors, zorder=3)
    for x_value, y_value in zip(df["mean_soluprot"], y, strict=True):
        ax2.plot([0, x_value], [y_value, y_value], color="#d0d5dd", linewidth=0.8, zorder=1)
    ax2.axvline(0.5, color="#3a7d44", linewidth=1.0, linestyle="--")
    ax2.set_xlim(0.15, 1.0)
    ax2.set_xlabel("Mean SoluProt")
    ax2.set_title("Soluble-expression score", fontsize=10, weight="bold")
    ax2.tick_params(axis="y", length=0, labelleft=False)

    fig.suptitle(
        _figure_title(summary),
        fontsize=11,
        weight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.018,
        "Targets are sorted by mean pLDDT. Dashed guides mark pLDDT = 85 and SoluProt = 0.5.",
        ha="center",
        fontsize=8,
        color="#475467",
    )

    out_png = FIG_DIR / "fig11_cath_curated_expansion.png"
    fig.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.close(fig)

    out_table = FIG_DIR / "table9_cath_curated_summary.tex"
    _write_summary_table(summary, out_table)

    print(f"wrote: {out_png}")
    print(f"wrote: {out_table}")


if __name__ == "__main__":
    main()
