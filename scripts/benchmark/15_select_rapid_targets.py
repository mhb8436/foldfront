#!/usr/bin/env python3
"""Select deterministic CATH targets for the RAPID manuscript refresh.

The selector is intentionally independent of runtime outputs. It scans CATH PDB
files, estimates the requested domain-chain length from the target identifier,
and writes a reproducible manifest for corrected-chain CATH re-screening and
structural-context ablation.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_ROOT = Path(
    os.environ.get("PROTEIN_PIPELINE_DATA_ROOT")
    or os.environ.get("PROTEIN_PIPELINE_ROOT")
    or PROJECT_ROOT
).resolve()
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark" / "results"
SPLIT_ORDER = ["test", "val", "train"]
LENGTH_BINS = [
    ("070_120", 70, 120),
    ("121_180", 121, 180),
    ("181_240", 181, 240),
    ("241_300", 241, 300),
]
AA3 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}


def cath_chain_from_target(target: str) -> str | None:
    clean = target.strip()
    if len(clean) < 5:
        return None
    chain = clean[4]
    return chain if chain.strip() else None


def first_model_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if not any(line.startswith("MODEL") for line in lines):
        return lines
    out: list[str] = []
    in_first = False
    seen = False
    for line in lines:
        if line.startswith("MODEL"):
            if seen:
                break
            seen = True
            in_first = True
            continue
        if line.startswith("ENDMDL"):
            if in_first:
                break
            continue
        if in_first:
            out.append(line)
    return out


def chain_sequences(pdb_path: Path) -> dict[str, str]:
    text = pdb_path.read_text(encoding="utf-8", errors="ignore")
    residues: dict[str, list[tuple[tuple[str, str], str]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for line in first_model_lines(text):
        if not line.startswith("ATOM"):
            continue
        atom = line[12:16].strip()
        if atom != "CA":
            continue
        resname = line[17:20].strip().upper()
        aa = AA3.get(resname)
        if not aa:
            continue
        chain = (line[21].strip() or "_")
        resseq = line[22:26].strip()
        icode = line[26:27].strip()
        key = (chain, resseq, icode, resname)
        if key in seen:
            continue
        seen.add(key)
        residues.setdefault(chain, []).append(((resseq, icode), aa))
    return {
        chain: "".join(aa for _, aa in sorted(items, key=lambda item: item[0]))
        for chain, items in residues.items()
    }


def length_bin(length: int) -> str | None:
    for label, low, high in LENGTH_BINS:
        if low <= length <= high:
            return label
    return None


def scan_targets(source_root: Path, splits: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in splits:
        split_dir = source_root / f"cath_{split}"
        if not split_dir.is_dir():
            continue
        for pdb_path in sorted(split_dir.glob("*.pdb")):
            target = pdb_path.stem
            requested_chain = cath_chain_from_target(target)
            seq_by_chain = chain_sequences(pdb_path)
            chain = requested_chain if requested_chain in seq_by_chain else None
            if chain is None and seq_by_chain:
                chain = max(seq_by_chain, key=lambda c: len(seq_by_chain[c]))
            sequence = seq_by_chain.get(chain or "", "")
            length = len(sequence)
            bin_label = length_bin(length)
            rows.append(
                {
                    "split": split,
                    "target": target,
                    "pdb_path": str(pdb_path),
                    "requested_chain": requested_chain or "",
                    "selected_chain": chain or "",
                    "length": length,
                    "length_bin": bin_label or "",
                    "eligible": bool(bin_label and sequence),
                }
            )
    return rows


def completed_targets(paths: list[Path]) -> set[str]:
    out: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                target = str(row.get("target") or "").strip()
                if target:
                    out.add(target)
    return out


def round_robin_select(rows: list[dict[str, Any]], limit: int) -> set[str]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("eligible"):
            continue
        key = (str(row["split"]), str(row["length_bin"]))
        buckets.setdefault(key, []).append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda r: str(r["target"]))

    selected: set[str] = set()
    while len(selected) < limit:
        changed = False
        for split in SPLIT_ORDER:
            for bin_label, _, _ in LENGTH_BINS:
                bucket = buckets.get((split, bin_label), [])
                while bucket and str(bucket[0]["target"]) in selected:
                    bucket.pop(0)
                if not bucket:
                    continue
                selected.add(str(bucket.pop(0)["target"]))
                changed = True
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        if not changed:
            break
    return selected


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "split",
        "target",
        "pdb_path",
        "requested_chain",
        "selected_chain",
        "length",
        "length_bin",
        "eligible",
        "previously_completed",
        "selected_for_cath_rescreen",
        "selected_for_structural_context",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--splits", default="test,val,train")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--structural-limit", type=int, default=8)
    parser.add_argument(
        "--exclude-completed",
        action="store_true",
        help="Prefer targets not listed in prior public-release CATH summaries.",
    )
    parser.add_argument(
        "--completed-csv",
        action="append",
        default=[
            str(PROJECT_ROOT / "public_data" / "cath_73" / "cath_73_per_target_summary.csv"),
            str(PROJECT_ROOT / "public_data" / "cath_curated" / "curated_per_target_summary.csv"),
        ],
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_RESULTS_DIR / "rapid_target_manifest.csv"),
    )
    args = parser.parse_args(argv)

    splits = [item.strip() for item in str(args.splits).split(",") if item.strip()]
    scanned = scan_targets(Path(args.source_root).resolve(), splits)
    completed = completed_targets([Path(p) for p in args.completed_csv])

    candidates = [
        row
        for row in scanned
        if row.get("eligible")
        and (not args.exclude_completed or str(row["target"]) not in completed)
    ]
    selected = round_robin_select(candidates, int(args.limit))
    structural = round_robin_select(
        [row for row in candidates if str(row["target"]) in selected],
        int(args.structural_limit),
    )

    for row in scanned:
        target = str(row["target"])
        row["previously_completed"] = target in completed
        row["selected_for_cath_rescreen"] = target in selected
        row["selected_for_structural_context"] = target in structural

    write_manifest(Path(args.output), scanned)
    print(f"wrote: {args.output}")
    print(f"scanned={len(scanned)} eligible={sum(1 for r in scanned if r.get('eligible'))}")
    print(f"selected_for_cath_rescreen={len(selected)}")
    print(f"selected_for_structural_context={len(structural)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
