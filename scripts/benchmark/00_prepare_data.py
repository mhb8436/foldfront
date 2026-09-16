#!/usr/bin/env python3
"""
Build master CSV from cath_outputs/ for the surrogate-model benchmark.

Schema:
    target          (str)  - CATH target id, e.g. "1b65A00"
    run_id          (str)  - "cath_test_<target>"
    tier            (int)  - 30 / 50 / 70  (conservation %)
    seq_id          (str)  - ProteinMPNN sample id, e.g. "target:7"
    sequence        (str)  - amino acid sequence
    plddt           (float) - ColabFold mean pLDDT  (NaN if AF2 failed)
    soluprot        (float) - SoluProt score        (NaN if missing)

The script is idempotent: it overwrites the output CSV each run.
"""

from __future__ import annotations

import csv
import json
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
CATH_OUTPUTS = PROJECT_ROOT / "cath_outputs"
DATA_OUT = PROJECT_ROOT / "data" / "benchmark"
OUT_CSV = DATA_OUT / "cath_pilot_dataset.csv"

FIELDS = ["target", "run_id", "tier", "seq_id", "sequence", "plddt", "soluprot"]


def parse_fasta(text: str) -> dict[str, str]:
    """Return a {seq_id -> sequence} dict from a FASTA file."""
    out: dict[str, str] = {}
    cur_id: str | None = None
    cur_chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if cur_id is not None:
                out[cur_id] = "".join(cur_chunks)
            # ProteinMPNN headers are just the seq id; ColabFold uses bare id too
            cur_id = line[1:].strip().split()[0]
            cur_chunks = []
        else:
            cur_chunks.append(line)
    if cur_id is not None:
        out[cur_id] = "".join(cur_chunks)
    return out


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[warn] failed to parse {path}: {exc}", file=sys.stderr)
        return None


def collect_target(run_dir: Path) -> list[dict]:
    """Build per-sequence rows for one CATH target."""
    target = run_dir.name.replace("cath_test_", "")
    rows: list[dict] = []
    tiers_dir = run_dir / "tiers"
    if not tiers_dir.is_dir():
        return rows

    for tier_dir in sorted(tiers_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        try:
            tier = int(tier_dir.name)
        except ValueError:
            continue

        # designs_filtered.fasta uses 'target:N' headers that match AF2/SoluProt keys.
        # designs.fasta is the raw ProteinMPNN output with 'N' headers - fallback only.
        fasta = tier_dir / "designs_filtered.fasta"
        if not fasta.exists():
            fasta = tier_dir / "designs.fasta"
        if not fasta.exists():
            print(f"[warn] no fasta in {tier_dir}", file=sys.stderr)
            continue
        seq_map = parse_fasta(fasta.read_text(encoding="utf-8"))

        af2 = load_json(tier_dir / "af2_scores.json") or {}
        solu = load_json(tier_dir / "soluprot.json") or {}

        plddt_scores: dict[str, float] = {
            str(k): float(v) for k, v in (af2.get("scores") or {}).items()
        }
        solu_scores: dict[str, float] = {
            str(k): float(v) for k, v in (solu.get("scores") or {}).items()
        }

        for seq_id, sequence in seq_map.items():
            if not sequence:
                continue
            rows.append(
                {
                    "target": target,
                    "run_id": run_dir.name,
                    "tier": tier,
                    "seq_id": seq_id,
                    "sequence": sequence,
                    "plddt": plddt_scores.get(seq_id),
                    "soluprot": solu_scores.get(seq_id),
                }
            )
    return rows


def main() -> int:
    if not CATH_OUTPUTS.is_dir():
        print(f"[fatal] {CATH_OUTPUTS} not found", file=sys.stderr)
        return 1

    DATA_OUT.mkdir(parents=True, exist_ok=True)

    runs = sorted(p for p in CATH_OUTPUTS.iterdir() if p.is_dir())
    print(f"Scanning {len(runs)} CATH runs in {CATH_OUTPUTS}")
    all_rows: list[dict] = []
    per_target_summary: list[tuple[str, int, int, int]] = []

    for run in runs:
        rows = collect_target(run)
        n_total = len(rows)
        n_plddt = sum(1 for r in rows if r["plddt"] is not None)
        n_solu = sum(1 for r in rows if r["soluprot"] is not None)
        per_target_summary.append((run.name, n_total, n_plddt, n_solu))
        all_rows.extend(rows)
        print(f"  {run.name}: rows={n_total}, pLDDT={n_plddt}, SoluProt={n_solu}")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    summary_path = DATA_OUT / "cath_pilot_dataset_summary.json"
    summary = {
        "n_targets": len(runs),
        "n_rows": len(all_rows),
        "n_plddt_labels": sum(1 for r in all_rows if r["plddt"] is not None),
        "n_soluprot_labels": sum(1 for r in all_rows if r["soluprot"] is not None),
        "per_target": [
            {"run_id": rid, "n_rows": n, "n_plddt": p, "n_solu": s}
            for rid, n, p, s in per_target_summary
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print(f"Master CSV : {OUT_CSV} ({len(all_rows)} rows)")
    print(f"Summary    : {summary_path}")
    print(
        f"pLDDT labels: {summary['n_plddt_labels']}, "
        f"SoluProt labels: {summary['n_soluprot_labels']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
