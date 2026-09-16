#!/usr/bin/env python3
"""Collect AF2-budgeted surrogate-triage run summaries for the manuscript."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = PROJECT_ROOT / "data" / "benchmark" / "results"
DEFAULT_FIGURES = PROJECT_ROOT / "figures" / "benchmark"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _iter_run_dirs(output_root: Path, prefixes: list[str], run_ids: list[str]) -> list[Path]:
    selected: list[Path] = []
    for run_id in run_ids:
        path = output_root / run_id
        if path.is_dir():
            selected.append(path)
    for prefix in prefixes:
        selected.extend(sorted(path for path in output_root.glob(f"{prefix}*") if path.is_dir()))
    seen: set[Path] = set()
    out: list[Path] = []
    for path in selected:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def _rows_for_run(run_dir: Path) -> list[dict[str, object]]:
    request = _load_json(run_dir / "request.json")
    summary = _load_json(run_dir / "summary.json")
    rows: list[dict[str, object]] = []
    for scores_path in sorted(run_dir.glob("tiers/*/af2_scores.json")):
        scores = _load_json(scores_path)
        triage = scores.get("surrogate_triage") or {}
        if not triage.get("enabled"):
            continue
        before = int(
            triage.get("candidate_count_before_triage")
            or scores.get("candidate_count_before_budget")
            or 0
        )
        after = int(
            triage.get("candidate_count_after_budget")
            or triage.get("candidate_count_after_triage")
            or scores.get("candidate_count_after_budget")
            or 0
        )
        reduction = (1.0 - after / before) if before > 0 else None
        tier = scores_path.parent.name
        rows.append(
            {
                "run_id": run_dir.name,
                "target": run_dir.name.split("_")[-1],
                "tier": tier,
                "requested_policy": triage.get("requested_policy") or triage.get("model") or "",
                "selected_policy": triage.get("selected_policy") or "",
                "surrogate_models": ",".join(triage.get("models") or [triage.get("model") or ""]),
                "comparator_models": ",".join(triage.get("comparator_models") or triage.get("models") or []),
                "ensemble_models": ",".join(triage.get("ensemble_models") or []),
                "selection_strategy": triage.get("selection_strategy") or "",
                "initial_samples": int(triage.get("initial_samples") or request.get("surrogate_triage_initial_samples") or 0),
                "top_k": int(triage.get("top_k") or request.get("surrogate_triage_top_k") or 0),
                "candidate_count_before_triage": before,
                "candidate_count_after_triage": after,
                "af2_reduction_fraction": reduction,
                "af2_reduction_percent": (100.0 * reduction) if reduction is not None else None,
                "training_count": len(triage.get("training_ids") or []),
                "selected_top_count": len(triage.get("selected_top_ids") or []),
                "fitted_models": ",".join(triage.get("fitted_models") or []),
                "skipped": bool(triage.get("skipped")),
                "status_state": _load_json(run_dir / "status.json").get("state") or "",
                "summary_best_design": (summary.get("best_design") or {}).get("id") if isinstance(summary.get("best_design"), dict) else "",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "target",
        "tier",
        "requested_policy",
        "selected_policy",
        "surrogate_models",
        "comparator_models",
        "ensemble_models",
        "selection_strategy",
        "initial_samples",
        "top_k",
        "candidate_count_before_triage",
        "candidate_count_after_triage",
        "af2_reduction_fraction",
        "af2_reduction_percent",
        "training_count",
        "selected_top_count",
        "fitted_models",
        "skipped",
        "status_state",
        "summary_best_design",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_summary_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    usable = [row for row in rows if not row.get("skipped") and row.get("candidate_count_before_triage")]
    by_run: dict[str, list[dict[str, object]]] = {}
    for row in usable:
        by_run.setdefault(str(row["run_id"]), []).append(row)

    summaries: list[dict[str, object]] = []
    for run_id in sorted(by_run):
        run_rows = by_run[run_id]
        before = max(int(row["candidate_count_before_triage"] or 0) for row in run_rows)
        after = sum(int(row["candidate_count_after_triage"] or 0) for row in run_rows)
        reduction = (1.0 - after / before) if before else None
        status_values = {str(row.get("status_state") or "") for row in run_rows}
        summaries.append(
            {
                "run_id": run_id,
                "target": str(run_rows[0].get("target") or ""),
                "tiers": ",".join(sorted(str(row.get("tier") or "") for row in run_rows)),
                "requested_policy": ",".join(sorted({str(row.get("requested_policy") or "") for row in run_rows if row.get("requested_policy")})),
                "selected_policy": ",".join(sorted({str(row.get("selected_policy") or "") for row in run_rows if row.get("selected_policy")})),
                "surrogate_models": ",".join(sorted({str(row.get("surrogate_models") or "") for row in run_rows if row.get("surrogate_models")})),
                "comparator_models": ",".join(sorted({str(row.get("comparator_models") or "") for row in run_rows if row.get("comparator_models")})),
                "selection_strategy": ",".join(sorted({str(row.get("selection_strategy") or "") for row in run_rows if row.get("selection_strategy")})),
                "initial_samples": max(int(row.get("initial_samples") or 0) for row in run_rows),
                "top_k": max(int(row.get("top_k") or 0) for row in run_rows),
                "candidate_count_before_triage": before,
                "af2_records": after,
                "af2_reduction_fraction": reduction,
                "af2_reduction_percent": (100.0 * reduction) if reduction is not None else None,
                "bootstrap_labels": sum(int(row.get("training_count") or 0) for row in run_rows),
                "top_k_acquisitions": sum(int(row.get("selected_top_count") or 0) for row in run_rows),
                "status_state": "completed" if status_values == {"completed"} else ",".join(sorted(status_values)),
            }
        )
    return summaries


def _write_run_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "target",
        "tiers",
        "requested_policy",
        "selected_policy",
        "surrogate_models",
        "comparator_models",
        "selection_strategy",
        "initial_samples",
        "top_k",
        "candidate_count_before_triage",
        "af2_records",
        "af2_reduction_fraction",
        "af2_reduction_percent",
        "bootstrap_labels",
        "top_k_acquisitions",
        "status_state",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in _run_summary_rows(rows):
            writer.writerow(row)


def _write_table(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    usable = [row for row in rows if not row.get("skipped") and row.get("candidate_count_before_triage")]
    by_run: dict[str, list[dict[str, object]]] = {}
    for row in usable:
        by_run.setdefault(str(row["run_id"]), []).append(row)
    n_runs = len(by_run)
    n_tiers = len(usable)
    before = 0
    after = 0
    for run_rows in by_run.values():
        # Pooled triage writes the same pre-triage pool size into each tier score
        # file. Count it once per run, while summing the tier-specific evaluated
        # AF2 records after triage.
        before += max(int(row["candidate_count_before_triage"] or 0) for row in run_rows)
        after += sum(int(row["candidate_count_after_triage"] or 0) for row in run_rows)
    reduction = (100.0 * (1.0 - after / before)) if before else 0.0
    models = sorted({str(row.get("surrogate_models") or "") for row in usable if row.get("surrogate_models")})
    selected = sorted({str(row.get("selected_policy") or "") for row in usable if row.get("selected_policy")})
    model_text = ", ".join(model.replace(",", ", ") for model in models) or "-"
    selected_text = ", ".join(selected) or "-"
    text = "\n".join(
        [
            r"\begin{tabular}{llrrrrr}",
            r"\toprule",
            r"Comparator models & Selected policy & Runs & Tiers & Triage candidates & AF2 records & Reduction \\",
            r"\midrule",
            f"{model_text} & {selected_text} & {n_runs} & {n_tiers} & {before:,} & {after:,} & {reduction:.1f}\\% \\\\",
            r"\bottomrule",
            r"\end{tabular}",
            "",
            "% Reduction is relative to folding every candidate entering surrogate triage.",
        ]
    )
    path.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--prefix", action="append", default=[])
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument(
        "--csv-out",
        default=str(DEFAULT_RESULTS / "surrogate_triage_budget_summary.csv"),
    )
    parser.add_argument(
        "--run-csv-out",
        default=str(DEFAULT_RESULTS / "surrogate_triage_budget_run_summary.csv"),
    )
    parser.add_argument(
        "--table-out",
        default=str(DEFAULT_FIGURES / "table5_surrogate_triage_budget.tex"),
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output_root)
    prefixes = args.prefix or ["paper_surrogate_"]
    run_dirs = _iter_run_dirs(output_root, prefixes, args.run_id or [])
    rows: list[dict[str, object]] = []
    for run_dir in run_dirs:
        rows.extend(_rows_for_run(run_dir))
    _write_csv(Path(args.csv_out), rows)
    _write_run_csv(Path(args.run_csv_out), rows)
    _write_table(Path(args.table_out), rows)
    print(
        json.dumps(
            {
                "runs": len({row["run_id"] for row in rows}),
                "tiers": len(rows),
                "csv": str(Path(args.csv_out)),
                "run_csv": str(Path(args.run_csv_out)),
                "table": str(Path(args.table_out)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
