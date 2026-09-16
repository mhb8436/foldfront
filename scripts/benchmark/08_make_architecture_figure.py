#!/usr/bin/env python3
"""
Programmatically draw the protein_pipeline architecture as a paper figure.

Output: figures/benchmark/fig1_architecture.png

The diagram has four horizontal bands (top to bottom):
    1. Static web console (5 surfaces)
    2. MCP / HTTP backend tools
    3. Pipeline runner with the canonical stage chain
    4. External modeling services (the swappable layer)
    5. Run-centric artifact store

Arrows show data + control flow; dashed boxes mark the swappable boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
OUT_PATH = PROJECT_ROOT / "figures" / "benchmark" / "fig1_architecture.png"


def add_band(ax, y, height, label, color, fontsize=9, label_offset=-0.6):
    rect = FancyBboxPatch(
        (0.4, y),
        13.2,
        height,
        boxstyle="round,pad=0.04,rounding_size=0.18",
        facecolor=color,
        edgecolor="#222",
        linewidth=1.0,
    )
    ax.add_patch(rect)
    ax.text(
        0.4 + label_offset,
        y + height / 2,
        label,
        rotation=90,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="bold",
        color="#222",
    )


def add_box(ax, x, y, w, h, text, fc="#ffffff", ec="#222", fontsize=8.5,
            italic=False, dashed=False):
    style = "round,pad=0.02,rounding_size=0.10"
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=style,
        facecolor=fc,
        edgecolor=ec,
        linewidth=1.0,
        linestyle="--" if dashed else "-",
    )
    ax.add_patch(rect)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontstyle="italic" if italic else "normal",
        color="#222",
    )


def add_arrow(ax, x1, y1, x2, y2, color="#444", style="->,head_length=6,head_width=4"):
    arrow = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle=style,
        color=color,
        linewidth=1.0,
        mutation_scale=10,
    )
    ax.add_patch(arrow)


def draw():
    fig, ax = plt.subplots(figsize=(14, 9))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 14)
    ax.set_aspect("equal")
    ax.axis("off")

    add_band(ax, 12.0, 1.7, "Web console", "#dbeafe")
    add_band(ax, 10.0, 1.6, "MCP / HTTP backend", "#fef3c7")
    add_band(ax, 7.7, 1.9, "Pipeline runner", "#dcfce7")
    add_band(ax, 4.7, 2.5, "Swappable model services", "#fde2e2")
    add_band(ax, 2.0, 2.2, "Artifact store", "#f3e8ff")
    add_band(ax, 0.0, 1.6, "Agentic layer", "#ede9fe")

    consoles = ["Setup", "Workflow\nStudio", "Monitor", "Analyze", "RunPod\nAdmin"]
    cx = 1.2
    for c in consoles:
        add_box(ax, cx, 12.2, 2.3, 1.2, c, fc="#ffffff")
        cx += 2.5

    backend_tools = [
        "pipeline.run", "preflight", "status",
        "compare_runs", "list_artifacts", "generate_report",
    ]
    cx = 1.0
    for t in backend_tools:
        add_box(ax, cx, 10.2, 1.95, 1.1, t, fc="#ffffff", fontsize=8)
        cx += 2.10

    stages = ["msa", "rfd3", "bioemu", "design\n(MPNN)",
              "soluprot", "af2 / cf", "novelty"]
    cx = 0.95
    for s in stages:
        add_box(ax, cx, 7.95, 1.62, 1.4, s, fc="#ffffff")
        cx += 1.79
    ax.text(7.0, 7.65, "msa  →  rfd3  →  bioemu  →  design  →  soluprot  →  af2  →  novelty",
            ha="center", va="center", fontsize=8, color="#444", fontstyle="italic")

    services = [
        ("MMseqs2", "#ffffff"),
        ("RFdiffusion 3\n(swappable)", "#ffffff"),
        ("BioEmu\n(swappable)", "#ffffff"),
        ("ProteinMPNN\n(swappable)", "#ffffff"),
        ("SoluProt\n(swappable)", "#ffffff"),
        ("AF2 / ColabFold\n→ ESMFold / AF3", "#ffffff"),
        ("Rosetta Relax\n(swappable)", "#ffffff"),
    ]
    cx = 0.95
    for s, fc in services:
        add_box(ax, cx, 4.85, 1.62, 2.2, s, fc=fc, dashed=True, fontsize=8)
        cx += 1.79

    artifacts = [
        "request.json", "status.json", "events.jsonl",
        "tiers/<conservation>/", "af2/", "relax/",
        "summary.json", "report.md / report_ko.md",
    ]
    cx = 0.95
    for a in artifacts:
        add_box(ax, cx, 2.2, 1.55, 1.65, a, fc="#ffffff", fontsize=7.5)
        cx += 1.60

    agents = [
        "Reasoning agent\n(Gemini)",
        "Literature → Mask\nagent",
        "Multi-round\nactive-learning\nagent",
        "Local surrogate\n(RF + K-Means)",
    ]
    cx = 1.6
    for a in agents:
        add_box(ax, cx, 0.15, 2.7, 1.5, a, fc="#ffffff", fontsize=8)
        cx += 2.95

    add_arrow(ax, 7.0, 12.1, 7.0, 11.65)
    add_arrow(ax, 7.0, 10.05, 7.0, 9.6)
    add_arrow(ax, 7.0, 7.85, 7.0, 7.15)
    add_arrow(ax, 7.0, 4.75, 7.0, 4.45)
    add_arrow(ax, 13.6, 11.0, 13.85, 11.0)
    ax.text(13.85, 11.55, "JSON-RPC\n/ HTTP", fontsize=7, ha="left", color="#555")

    legend_handles = [
        mpatches.Patch(facecolor="#dbeafe", edgecolor="#222", label="UI surface"),
        mpatches.Patch(facecolor="#fef3c7", edgecolor="#222", label="Backend (MCP/HTTP)"),
        mpatches.Patch(facecolor="#dcfce7", edgecolor="#222", label="Pipeline runner"),
        mpatches.Patch(facecolor="#fde2e2", edgecolor="#222", linestyle="--",
                       label="Swappable model services"),
        mpatches.Patch(facecolor="#f3e8ff", edgecolor="#222", label="Run-centric artifacts"),
        mpatches.Patch(facecolor="#ede9fe", edgecolor="#222", label="Agentic layer"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8,
              ncol=2, bbox_to_anchor=(1.0, 0.0), frameon=True)

    fig.suptitle(
        "protein_pipeline architecture: web console → MCP backend → "
        "pipeline runner → swappable model services + run-centric artifacts",
        fontsize=11, y=0.985,
    )
    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=220, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    draw()
