from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import html
from pathlib import Path
from typing import Any

from .bio.alignment import global_alignment_mapping
from .models import SequenceRecord
from .storage import write_json


@dataclass(frozen=True)
class Mutation:
    chain: str
    pos: int  # 1-based, native numbering
    wt: str
    aa: str

    def compact(self) -> str:
        if self.chain:
            return f"{self.chain}:{self.wt}{self.pos}{self.aa}"
        return f"{self.wt}{self.pos}{self.aa}"


def _split_multichain(seq: str) -> list[str]:
    seq = str(seq or "").strip()
    if not seq:
        return []
    parts = [p.strip() for p in seq.split("/") if p.strip()]
    return parts if len(parts) > 1 else [seq]


def _default_chain_ids(n: int) -> list[str]:
    # A, B, C, ...
    return [chr(ord("A") + i) for i in range(max(0, n))]


def _safe_chain_order(chain_order: list[str] | None, n_parts: int) -> list[str]:
    if chain_order and len(chain_order) == n_parts:
        return list(chain_order)
    if chain_order and len(chain_order) != n_parts:
        # Mismatch; fall back to A/B/C... rather than emitting wrong chain labels.
        return _default_chain_ids(n_parts)
    return _default_chain_ids(n_parts) if n_parts > 1 else ["A"]


def _aligned_chars_by_native_pos(native: str, sample: str) -> list[str]:
    """
    Returns a list of length len(native) where each entry is the sample AA aligned
    to that native position (or '-' if the sample has a gap at that position).
    """
    if native == sample:
        return list(sample)
    if len(native) == len(sample) and "/" not in native and "/" not in sample:
        return list(sample)

    mapping = global_alignment_mapping(native, sample).mapping_query_to_target
    out: list[str] = []
    for idx, mapped in enumerate(mapping, start=1):
        if mapped is None:
            out.append("-")
            continue
        j = int(mapped) - 1
        out.append(sample[j] if 0 <= j < len(sample) else "-")
    return out


def _mutation_list(
    *,
    native: str,
    aligned_sample: list[str],
    chain: str,
) -> list[Mutation]:
    muts: list[Mutation] = []
    for i, (wt, aa) in enumerate(zip(native, aligned_sample), start=1):
        if aa == wt:
            continue
        muts.append(Mutation(chain=chain, pos=i, wt=wt, aa=aa))
    return muts


def _percentiles(values: list[int]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "p10": None, "p50": None, "p90": None}
    vals = sorted(values)
    n = len(vals)

    def pick(p: float) -> float:
        if n == 1:
            return float(vals[0])
        # Nearest-rank (1-indexed) but implemented with 0-index.
        k = int(round((p * (n - 1))))
        k = max(0, min(n - 1, k))
        return float(vals[k])

    return {
        "min": float(vals[0]),
        "max": float(vals[-1]),
        "mean": float(sum(vals) / n),
        "p10": pick(0.10),
        "p50": pick(0.50),
        "p90": pick(0.90),
    }


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def _write_mutations_by_position_svg(
    out_path: Path,
    *,
    positions_payload: dict[str, list[dict[str, Any]]],
    chain_order: list[str],
) -> None:
    width = 1200
    panel_h = 260
    margin_left = 60
    margin_right = 20
    margin_top = 30
    margin_bottom = 40

    chains = [c for c in chain_order if c in positions_payload]
    if not chains:
        return

    height = panel_h * len(chains)
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        ".axis{stroke:#333;stroke-width:1}",
        ".grid{stroke:#ddd;stroke-width:1;shape-rendering:crispEdges}",
        ".label{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;fill:#111;font-size:12px}",
        ".title{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;fill:#111;font-size:14px;font-weight:600}",
        "</style>",
        '<rect x="0" y="0" width="100%" height="100%" fill="white"/>',
    ]

    plot_w = width - margin_left - margin_right
    plot_h = panel_h - margin_top - margin_bottom

    for idx, chain_id in enumerate(chains):
        rows = positions_payload.get(chain_id) or []
        if not rows:
            continue
        y0 = idx * panel_h
        x0 = margin_left
        y_top = y0 + margin_top
        y_bottom = y0 + panel_h - margin_bottom

        L = len(rows)
        denom = max(1, L - 1)

        parts.append(f'<text x="{x0}" y="{y0 + 18}" class="title">chain {html.escape(chain_id)}</text>')
        parts.append(f'<line x1="{x0}" y1="{y_top}" x2="{x0}" y2="{y_bottom}" class="axis" />')
        parts.append(
            f'<line x1="{x0}" y1="{y_bottom}" x2="{x0 + plot_w}" y2="{y_bottom}" class="axis" />'
        )

        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            y = y_top + (1.0 - frac) * plot_h
            parts.append(f'<line x1="{x0}" y1="{_fmt(y)}" x2="{x0 + plot_w}" y2="{_fmt(y)}" class="grid" />')
            parts.append(
                f'<text x="{x0 - 8}" y="{_fmt(y + 4)}" text-anchor="end" class="label">{frac:.2f}</text>'
            )

        if L >= 200:
            step = 50
        elif L >= 100:
            step = 20
        else:
            step = 10
        ticks = sorted(set([1, L] + [p for p in range(1, L + 1) if p % step == 0]))
        for p in ticks:
            x = x0 + (p - 1) * (plot_w / denom)
            parts.append(f'<line x1="{_fmt(x)}" y1="{y_bottom}" x2="{_fmt(x)}" y2="{y_bottom + 5}" class="axis" />')
            parts.append(
                f'<text x="{_fmt(x)}" y="{y_bottom + 18}" text-anchor="middle" class="label">{p}</text>'
            )

        d: list[str] = []
        fixed_points: list[tuple[float, float, int, float]] = []
        for row in rows:
            pos = int(row.get("pos") or 0)
            mutated_fraction = float(row.get("mutated_fraction") or 0.0)
            is_fixed = bool(row.get("fixed"))
            if not pos:
                continue
            x = x0 + (pos - 1) * (plot_w / denom)
            y = y_top + (1.0 - max(0.0, min(1.0, mutated_fraction))) * plot_h
            d.append(("M" if not d else "L") + f"{_fmt(x)},{_fmt(y)}")
            if is_fixed:
                fixed_points.append((x, y, pos, mutated_fraction))

        parts.append(f'<path d="{" ".join(d)}" fill="none" stroke="#1f77b4" stroke-width="1.5" />')

        for x, y, pos, mutated_fraction in fixed_points:
            title = html.escape(f"{chain_id}:{pos} mutated_fraction={mutated_fraction:.3f}")
            parts.append(
                f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="3" fill="#d62728" stroke="white" stroke-width="0.8"><title>{title}</title></circle>'
            )

        parts.append(f'<text x="{x0}" y="{y0 + panel_h - 10}" class="label">pos →</text>')
        center_y = y0 + (panel_h / 2.0)
        parts.append(
            f'<text x="14" y="{_fmt(center_y)}" class="label" transform="rotate(-90 14,{_fmt(center_y)})">mutated_fraction</text>'
        )

    parts.append("</svg>\n")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def write_mutation_reports(
    tier_dir: Path,
    *,
    native: SequenceRecord | None,
    samples: list[SequenceRecord],
    fixed_positions_by_chain: dict[str, list[int]],
    design_chains: list[str] | None,
) -> dict[str, str]:
    """
    Writes mutation summary files under `tier_dir` comparing ProteinMPNN samples
    against the native (wild-type) sequence.

    Returns a dict of output paths (strings) to store in the pipeline summary.
    """
    if native is None or not native.sequence:
        return {}

    json_path = tier_dir / "mutation_report.json"
    by_pos_tsv = tier_dir / "mutations_by_position.tsv"
    by_seq_tsv = tier_dir / "mutations_by_sequence.tsv"
    by_pos_svg = tier_dir / "mutations_by_position.svg"

    native_parts = _split_multichain(native.sequence)
    sample_parts_all = [_split_multichain(s.sequence) for s in samples]
    n_parts = len(native_parts)
    chain_order = _safe_chain_order(design_chains, n_parts)

    # If any sample has an unexpected chain part count, fall back to single-chain reporting.
    if any(len(parts) != n_parts for parts in sample_parts_all):
        native_parts = ["".join(native_parts)]
        chain_order = ["A"]
        sample_parts_all = [["".join(parts)] for parts in sample_parts_all]
        n_parts = 1

    fixed_sets: dict[str, set[int]] = {k: set(int(x) for x in v) for k, v in (fixed_positions_by_chain or {}).items()}
    total_samples = len(samples)

    # Count aligned amino acids at each native position.
    aligned_counts: dict[str, list[Counter[str]]] = {}
    mutations_by_sequence_rows: list[dict[str, Any]] = []
    mutation_counts: list[int] = []

    for chain_idx, chain_id in enumerate(chain_order):
        native_seq = native_parts[chain_idx]
        aligned_counts[chain_id] = [Counter() for _ in range(len(native_seq))]

    for sample, parts in zip(samples, sample_parts_all):
        sample_muts: list[Mutation] = []
        for chain_idx, chain_id in enumerate(chain_order):
            native_seq = native_parts[chain_idx]
            sample_seq = parts[chain_idx]
            aligned = _aligned_chars_by_native_pos(native_seq, sample_seq)
            for i, aa in enumerate(aligned):
                aligned_counts[chain_id][i][aa] += 1
            sample_muts.extend(_mutation_list(native=native_seq, aligned_sample=aligned, chain=chain_id))

        mutation_counts.append(len(sample_muts))
        mutations_by_sequence_rows.append(
            {
                "id": str(sample.id),
                "mutations": ",".join(m.compact() for m in sample_muts),
                "num_mutations": len(sample_muts),
            }
        )

    positions_payload: dict[str, list[dict[str, Any]]] = {}
    for chain_idx, chain_id in enumerate(chain_order):
        native_seq = native_parts[chain_idx]
        chain_fixed = fixed_sets.get(chain_id, set())
        pos_rows: list[dict[str, Any]] = []
        for i, wt in enumerate(native_seq, start=1):
            counts = aligned_counts[chain_id][i - 1]
            wt_count = int(counts.get(wt, 0))
            gap_count = int(counts.get("-", 0))
            mutated = total_samples - wt_count
            top_mutants = [
                {"aa": aa, "count": int(c)}
                for aa, c in counts.most_common()
                if aa not in {wt} and aa != ""
            ]
            pos_rows.append(
                {
                    "pos": i,
                    "wt": wt,
                    "fixed": (i in chain_fixed),
                    "counts": dict(counts),
                    "wt_count": wt_count,
                    "gap_count": gap_count,
                    "mutated_count": mutated,
                    "mutated_fraction": (mutated / total_samples) if total_samples else 0.0,
                    "top_mutants": top_mutants[:10],
                }
            )
        positions_payload[chain_id] = pos_rows

    payload = {
        "native_id": native.id,
        "native_header": native.header,
        "chain_order": chain_order,
        "sample_count": total_samples,
        "mutation_counts": {
            "per_sample": _percentiles(mutation_counts),
        },
        "positions": positions_payload,
    }
    write_json(json_path, payload)

    # Write TSVs for quick inspection.
    lines = ["chain\tpos\twt\tfixed\tmutated_count\tmutated_fraction\ttop_mutants"]
    for chain_id in chain_order:
        for row in positions_payload.get(chain_id, []):
            top = ";".join(f"{m['aa']}:{m['count']}" for m in (row.get("top_mutants") or []))
            lines.append(
                f"{chain_id}\t{row['pos']}\t{row['wt']}\t{int(bool(row['fixed']))}\t{row['mutated_count']}\t{row['mutated_fraction']:.4f}\t{top}"
            )
    by_pos_tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    seq_lines = ["id\tnum_mutations\tmutations"]
    for row in mutations_by_sequence_rows:
        seq_lines.append(f"{row['id']}\t{row['num_mutations']}\t{row['mutations']}")
    by_seq_tsv.write_text("\n".join(seq_lines) + "\n", encoding="utf-8")

    _write_mutations_by_position_svg(by_pos_svg, positions_payload=positions_payload, chain_order=chain_order)

    return {
        "mutation_report_path": str(json_path),
        "mutations_by_position_tsv": str(by_pos_tsv),
        "mutations_by_sequence_tsv": str(by_seq_tsv),
        "mutations_by_position_svg": str(by_pos_svg),
    }
