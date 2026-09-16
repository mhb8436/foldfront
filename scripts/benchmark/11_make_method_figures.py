#!/usr/bin/env python3
"""
Generate two method-section schematics for the manuscript.

Outputs:
    figures/benchmark/fig1_pipeline_overview.png
    figures/benchmark/fig2_active_learning_loop.png

Run:
    python scripts/benchmark/11_make_method_figures.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIG_DIR = PROJECT_ROOT / "figures" / "benchmark"

COLOR_HEAVY_GPU_STAGE = "#F4B8AB"
COLOR_CHEAP_GATE_STAGE = "#FFE5A0"
COLOR_NEUTRAL_STAGE = "#D9E2F3"
COLOR_DIVERSIFICATION_LEVER = "#A4C8A4"
COLOR_LOOP_DATA = "#E0E7F1"
COLOR_LOOP_AF2 = "#F4B8AB"
COLOR_LOOP_SURROGATE = "#A4C8A4"
EDGE = "#222"


def stage_box(ax, x, y, w, h, label, color, fs=10):
    box = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.03,rounding_size=0.12",
        facecolor=color,
        edgecolor=EDGE,
        linewidth=1.1,
    )
    ax.add_patch(box)
    ax.text(
        x + w / 2,
        y + h / 2,
        label,
        ha="center",
        va="center",
        fontsize=fs,
        fontweight="bold",
        color="#222",
    )


def arrow(ax, x1, y1, x2, y2, *, style="->", lw=1.2, color="#333", ls="-"):
    a = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle=style,
        mutation_scale=12,
        linewidth=lw,
        color=color,
        linestyle=ls,
    )
    ax.add_patch(a)


def lever_box(ax, x1, x2, y, text, *, h=0.72, fs=8.3):
    w = x2 - x1
    rect = FancyBboxPatch(
        (x1, y),
        w,
        h,
        boxstyle="round,pad=0.04,rounding_size=0.12",
        facecolor=COLOR_DIVERSIFICATION_LEVER,
        edgecolor=EDGE,
        linewidth=1.0,
        alpha=0.85,
    )
    ax.add_patch(rect)
    ax.text(
        x1 + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fs,
        color="#1d1d1d",
        linespacing=1.18,
    )


def step_box(ax, x, y, w, h, title, body, color, *, title_fs=10, body_fs=8.2):
    rect = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.04,rounding_size=0.12",
        facecolor=color,
        edgecolor=EDGE,
        linewidth=1.1,
    )
    ax.add_patch(rect)
    ax.text(
        x + w / 2,
        y + h - 0.17,
        title,
        ha="center",
        va="top",
        fontsize=title_fs,
        fontweight="bold",
        color="#1d1d1d",
    )
    ax.text(
        x + w / 2,
        y + h * 0.43,
        body,
        ha="center",
        va="center",
        fontsize=body_fs,
        color="#222",
        linespacing=1.25,
    )


def draw_pipeline_overview() -> Path:
    fig, ax = plt.subplots(figsize=(12.8, 6.2))
    ax.set_xlim(0, 12.8)
    ax.set_ylim(0, 6.2)
    ax.axis("off")

    def box(
        x,
        y,
        w,
        h,
        title,
        body,
        color,
        *,
        title_fs=8.0,
        body_fs=6.7,
        lw=1.05,
        ls="-",
        ha="center",
        title_y=0.68,
        body_y=0.32,
    ):
        rect = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.05,rounding_size=0.12",
            facecolor=color,
            edgecolor=EDGE,
            linewidth=lw,
            linestyle=ls,
        )
        ax.add_patch(rect)
        ax.text(
            x + (0.16 if ha == "left" else w / 2),
            y + h * title_y,
            title,
            ha=ha,
            va="center",
            fontsize=title_fs,
            fontweight="bold",
            color="#1f2933",
        )
        ax.text(
            x + (0.16 if ha == "left" else w / 2),
            y + h * body_y,
            body,
            ha=ha,
            va="center",
            fontsize=body_fs,
            color="#263238",
            linespacing=1.2,
        )

    ax.text(
        6.4,
        5.88,
        "RAPID run_id-centered redesign substrate",
        ha="center",
        va="center",
        fontsize=12.5,
        fontweight="bold",
    )
    ax.text(
        6.4,
        5.56,
        "Structural-context exploration, surrogate triage, and experimental-feedback evolution use the same run-scoped artifact contract.",
        ha="center",
        va="center",
        fontsize=8.0,
        color="#4b5563",
    )

    modules = [
        ("Input and context", "MMseqs2; optional RFD3/BioEmu", "#F7D6CE", "--"),
        ("Design and filtering", "ProteinMPNN; SoluProt", COLOR_CHEAP_GATE_STAGE, "-"),
        ("Evaluation and reporting", "AF2/ColabFold; novelty", COLOR_NEUTRAL_STAGE, "-"),
    ]
    ax.text(
        0.55,
        5.10,
        "Replaceable computational modules",
        ha="left",
        va="center",
        fontsize=8.1,
        color="#4b5563",
        fontweight="bold",
    )
    module_y = 4.42
    module_w = 2.10
    module_h = 0.48
    module_gap = 0.24
    module_x0 = 0.75
    for i, (title, body, color, ls) in enumerate(modules):
        x = module_x0 + i * (module_w + module_gap)
        box(x, module_y, module_w, module_h, title, body, color, title_fs=6.8, body_fs=5.6, lw=0.8, ls=ls)
        if i < len(modules) - 1:
            arrow(ax, x + module_w + 0.02, module_y + module_h / 2, x + module_w + module_gap - 0.02, module_y + module_h / 2, lw=0.65, color="#6b7280")

    store_x, store_y, store_w, store_h = 2.40, 1.45, 5.55, 2.60
    store = FancyBboxPatch(
        (store_x, store_y),
        store_w,
        store_h,
        boxstyle="round,pad=0.07,rounding_size=0.16",
        facecolor="#E9EEF7",
        edgecolor="#1f2933",
        linewidth=1.45,
    )
    ax.add_patch(store)
    ax.text(
        store_x + store_w / 2,
        store_y + store_h - 0.28,
        "run-scoped artifact contract",
        ha="center",
        va="top",
        fontsize=10.6,
        fontweight="bold",
        color="#19324d",
    )
    run_pill = FancyBboxPatch(
        (store_x + store_w / 2 - 0.62, store_y + store_h - 0.80),
        1.38,
        0.34,
        boxstyle="round,pad=0.03,rounding_size=0.12",
        facecolor="white",
        edgecolor="#6b7280",
        linewidth=0.9,
    )
    ax.add_patch(run_pill)
    ax.text(
        store_x + store_w / 2,
        store_y + store_h - 0.61,
        "run_id",
        ha="center",
        va="center",
        fontsize=8.4,
        fontweight="bold",
        color="#1f2933",
    )

    artifacts = [
        ("request", "operator inputs"),
        ("provenance records", "run status / trace"),
        ("stage outputs", "PDB / FASTA / scores"),
        ("surrogate labels", "bootstrap / predictions"),
        ("AF2 evaluations", "pLDDT / structure files"),
        ("experiment records", "assay labels"),
        ("model artifacts", "features / fitted models"),
        ("final summary", "ranked candidates"),
    ]
    art_w, art_h = 1.95, 0.38
    for idx, (name, detail) in enumerate(artifacts):
        col = idx % 2
        row = idx // 2
        x = store_x + 0.42 + col * 2.62
        y = store_y + store_h - 1.16 - row * 0.46
        rect = FancyBboxPatch(
            (x, y),
            art_w,
            art_h,
            boxstyle="round,pad=0.03,rounding_size=0.06",
            facecolor="white",
            edgecolor="#9aa7b8",
            linewidth=0.65,
        )
        ax.add_patch(rect)
        ax.text(x + 0.10, y + art_h * 0.63, name, ha="left", va="center", fontsize=6.2, fontweight="bold", color="#263238")
        ax.text(x + 0.10, y + art_h * 0.25, detail, ha="left", va="center", fontsize=5.3, color="#4b5563")

    arrow(ax, module_x0 + module_w * 1.55 + module_gap, module_y - 0.05, store_x + store_w / 2, store_y + store_h + 0.10, lw=1.0, color="#5b6470")
    ax.text(
        store_x + store_w / 2 + 0.25,
        4.24,
        "typed artifacts",
        ha="left",
        va="center",
        fontsize=6.4,
        color="#4b5563",
        style="italic",
    )

    right_x = 8.65
    right_w = 3.45
    outputs = [
        (
            4.20,
            "Structural-context exploration",
            "single / RFD3 / BioEmu contexts\ncandidate distribution analyses",
            "#D8EBC8",
            "#4f7d3a",
        ),
        (
            3.08,
            "Resource-aware surrogate triage",
            "surrogate labels + fixed\nAF2 validation budget",
            "#DFF2F1",
            "#0F766E",
        ),
        (
            1.96,
            "Experimental-feedback evolution",
            "assay outcomes update\nfuture preference records",
            "#FFF0C8",
            "#8A6D00",
        ),
        (
            0.84,
            "Rerun, analysis, replacement",
            "safe reruns; retrospective analysis\nbackend/provider swaps",
            "#F7D6CE",
            "#B45309",
        ),
    ]
    for y, title, body, color, arrow_color in outputs:
        box(
            right_x,
            y,
            right_w,
            0.72,
            title,
            body,
            color,
            title_fs=7.9,
            body_fs=6.2,
            lw=1.05,
            ha="left",
            title_y=0.70,
            body_y=0.34,
        )
        arrow(ax, store_x + store_w + 0.10, y + 0.36, right_x - 0.10, y + 0.36, lw=1.0, color=arrow_color)

    arrow(ax, right_x - 0.10, 2.08, store_x + store_w - 0.20, store_y + 0.30, lw=1.0, color="#8A6D00", ls="--")
    ax.text(
        0.58,
        0.42,
        "The linear workflow is only the execution order; the persistent unit is the run_id-centered redesign record.",
        ha="left",
        va="center",
        fontsize=7.0,
        color="#4b5563",
    )

    out = FIG_DIR / "fig1_pipeline_overview.png"
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    fig.savefig(out.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return out


def draw_active_learning_loop() -> Path:
    fig, ax = plt.subplots(figsize=(13.8, 6.2))
    ax.set_xlim(0, 13.8)
    ax.set_ylim(0, 6.2)
    ax.axis("off")

    ax.text(
        6.9,
        5.78,
        "AF2-budgeted surrogate triage",
        ha="center",
        va="center",
        fontsize=12.5,
        fontweight="bold",
    )
    ax.text(
        6.9,
        5.45,
        "One standard pipeline run: conservation-tier candidates are pooled before one shared AF2/ColabFold triage budget.",
        ha="center",
        va="center",
        fontsize=8.8,
        color="#555",
    )

    box_h = 1.08
    box_w = 2.28
    y_top = 4.05
    y_bottom = 2.25
    step_x = [0.45, 3.05, 5.65, 8.25, 10.85]

    step_box(
        ax,
        step_x[0],
        y_top,
        box_w,
        box_h,
        "Step 1",
        "Tiered design pool\nProteinMPNN\n3 × 3333 ≈ 9999",
        COLOR_LOOP_DATA,
    )

    step_box(
        ax,
        step_x[1],
        y_top,
        box_w,
        box_h,
        "SoluProt gate",
        "soluprot_cutoff = 0.5\npool tiers together\n\u2192 P candidates",
        COLOR_CHEAP_GATE_STAGE,
    )
    arrow(
        ax,
        step_x[0] + box_w,
        y_top + box_h / 2,
        step_x[1],
        y_top + box_h / 2,
    )

    step_box(
        ax,
        step_x[2],
        y_top,
        box_w,
        box_h,
        "Step 2",
        "K-means bootstrap\nESM-2 8M (320-D)\nN_TRAIN = 30",
        COLOR_LOOP_DATA,
    )
    arrow(
        ax,
        step_x[1] + box_w,
        y_top + box_h / 2,
        step_x[2],
        y_top + box_h / 2,
    )

    ax.text(
        step_x[2] + box_w / 2,
        y_top - 0.34,
        "30 labelled seeds + pooled unlabeled tier candidates",
        ha="center",
        va="center",
        fontsize=8.5,
        color="#444",
        style="italic",
    )

    step_box(
        ax,
        step_x[3],
        y_top,
        box_w,
        box_h,
        "Step 3",
        "AF2 on training set\n30 AF2 calls\nlabel: pLDDT",
        COLOR_LOOP_AF2,
    )
    arrow(
        ax,
        step_x[2] + box_w,
        y_top + box_h / 2,
        step_x[3],
        y_top + box_h / 2,
    )

    step_box(
        ax,
        step_x[3],
        y_bottom,
        box_w,
        box_h * 1.16,
        "Step 4",
        "Fit and compare\nRF/Ridge/GBM policies\nrank pooled remainder",
        COLOR_LOOP_SURROGATE,
        body_fs=7.9,
    )
    arrow(
        ax,
        step_x[3] + box_w / 2,
        y_top,
        step_x[3] + box_w / 2,
        y_bottom + box_h * 1.16,
    )

    arrow(
        ax,
        step_x[2] + box_w,
        y_top + 0.08,
        step_x[3],
        y_bottom + box_h * 1.16 / 2,
        lw=1.0,
        color="#558055",
        ls="--",
    )
    step_box(
        ax,
        step_x[4],
        y_top,
        box_w,
        box_h,
        "Step 5",
        "AF2 on pooled Top-K\nTOP_K = 20\n20 AF2 calls",
        COLOR_LOOP_AF2,
    )
    arrow(
        ax,
        step_x[3] + box_w,
        y_bottom + box_h * 1.16 / 2,
        step_x[4],
        y_top + box_h / 2,
        color="#555",
    )

    step_box(
        ax,
        step_x[4],
        y_bottom,
        box_w,
        box_h * 1.16,
        "Step 6",
        "Composite score\n0.4\u00b7pLDDT + 30\u00b7SoluProt\n\u2013 relax_penalty\n\u2192 best design",
        COLOR_LOOP_DATA,
        body_fs=7.9,
    )
    arrow(
        ax,
        step_x[4] + box_w / 2,
        y_top,
        step_x[4] + box_w / 2,
        y_bottom + box_h * 1.16,
    )

    ax.text(
        6.9,
        1.05,
        "Pooled triage AF2 calls = 30 (training) + 20 (top-K) = 50",
        ha="center",
        va="center",
        fontsize=10,
        fontweight="bold",
        color="#1d1d1d",
    )
    ax.text(
        6.9,
        0.58,
        "Full-folding baseline scales with P; RAPID keeps one shared AF2 budget across selected conservation tiers",
        ha="center",
        va="center",
        fontsize=9,
        color="#333",
        style="italic",
    )

    out = FIG_DIR / "fig2_active_learning_loop.png"
    fig.savefig(out, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    p1 = draw_pipeline_overview()
    p2 = draw_active_learning_loop()
    print(f"wrote: {p1}")
    print(f"wrote: {p2}")


if __name__ == "__main__":
    main()
