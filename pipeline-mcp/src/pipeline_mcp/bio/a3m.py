from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import math

from .fasta import FastaRecord
from .fasta import parse_fasta
from .fasta import to_fasta


def decode_a3m_gz_b64(a3m_gz_b64: str) -> str:
    raw = base64.b64decode(a3m_gz_b64)
    return gzip.decompress(raw).decode("utf-8", errors="replace")


def strip_insertions(a3m_seq: str) -> str:
    return "".join(ch for ch in a3m_seq if not ("a" <= ch <= "z"))


@dataclass(frozen=True)
class Conservation:
    query_length: int
    scores: list[float]
    fixed_positions_by_tier: dict[float, list[int]]


def _normalize_records(a3m_text: str) -> list[FastaRecord]:
    records = parse_fasta(a3m_text)
    normalized: list[FastaRecord] = []
    for rec in records:
        normalized.append(FastaRecord(header=rec.header, sequence=strip_insertions(rec.sequence)))
    return normalized


def conservation_scores(a3m_text: str, *, weights: list[float] | None = None) -> list[float]:
    records = _normalize_records(a3m_text)
    query = records[0].sequence
    L = len(query)
    if L <= 0:
        raise ValueError("A3M query sequence is empty")

    counts: list[dict[str, float]] = [dict() for _ in range(L)]
    totals: list[float] = [0.0 for _ in range(L)]

    hits = records[1:]
    hit_weights: list[float] = [1.0 for _ in hits] if weights is None else [float(x) for x in weights]
    if len(hit_weights) != len(hits):
        raise ValueError(f"Expected weights for {len(hits)} hits, got {len(hit_weights)}")

    for rec, w in zip(hits, hit_weights, strict=False):
        seq = rec.sequence
        if len(seq) != L:
            continue
        for i, ch in enumerate(seq):
            if ch in {"-", "."}:
                continue
            up = ch.upper()
            if not up.isalpha():
                continue
            totals[i] += float(w)
            counts[i][up] = counts[i].get(up, 0.0) + float(w)

    scores: list[float] = []
    for i in range(L):
        if float(totals[i]) <= 0.0:
            scores.append(0.0)
            continue
        max_count = max(counts[i].values(), default=0.0)
        scores.append(float(max_count) / float(totals[i]))
    return scores


def fixed_positions(
    scores: list[float],
    tiers: list[float],
    *,
    mode: str = "quantile",
) -> dict[float, list[int]]:
    if mode not in {"quantile", "threshold"}:
        raise ValueError("mode must be 'quantile' or 'threshold'")

    L = len(scores)
    if L == 0:
        return {tier: [] for tier in tiers}

    pos_scores = [(idx + 1, float(score)) for idx, score in enumerate(scores)]
    pos_scores.sort(key=lambda t: (-t[1], t[0]))

    out: dict[float, list[int]] = {}
    for tier in tiers:
        t = float(tier)
        if mode == "threshold":
            fixed = [pos for pos, score in pos_scores if score >= t]
        else:
            k = int(math.floor(L * t))
            if k < 0:
                k = 0
            if k > L:
                k = L
            fixed = [pos for pos, _score in pos_scores[:k]]
        fixed.sort()
        out[tier] = fixed
    return out


def compute_conservation(
    a3m_text: str,
    *,
    tiers: list[float],
    mode: str = "quantile",
    weights: list[float] | None = None,
) -> Conservation:
    records = _normalize_records(a3m_text)
    query_len = len(records[0].sequence)
    scores = conservation_scores(a3m_text, weights=weights)
    fixed_by_tier = fixed_positions(scores, tiers, mode=mode)
    return Conservation(query_length=query_len, scores=scores, fixed_positions_by_tier=fixed_by_tier)


def _percentiles(values: list[float], ps: list[int]) -> dict[str, float] | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    n = len(vals)

    def pick(p: int) -> float:
        if n == 1:
            return vals[0]
        x = (max(0, min(100, int(p))) / 100.0) * (n - 1)
        lo = int(math.floor(x))
        hi = int(math.ceil(x))
        if lo == hi:
            return vals[lo]
        frac = x - lo
        return (1.0 - frac) * vals[lo] + frac * vals[hi]

    out: dict[str, float] = {
        "min": vals[0],
        "max": vals[-1],
        "mean": sum(vals) / n,
    }
    for p in ps:
        out[f"p{int(p)}"] = pick(int(p))
    return out


def msa_quality(a3m_text: str) -> dict[str, object]:
    records = _normalize_records(a3m_text)
    query = records[0].sequence
    L = len(query)
    if L <= 0:
        raise ValueError("A3M query sequence is empty")

    total_hits = max(0, len(records) - 1)
    usable_hits = 0
    length_mismatch_hits = 0
    coverages: list[float] = []
    identities: list[float] = []
    depths: list[int] = [0 for _ in range(L)]

    for rec in records[1:]:
        seq = rec.sequence
        if len(seq) != L:
            length_mismatch_hits += 1
            continue
        usable_hits += 1

        non_gap = 0
        matches = 0
        for i, ch in enumerate(seq):
            if ch in {"-", "."}:
                continue
            up = ch.upper()
            if not up.isalpha():
                continue
            non_gap += 1
            depths[i] += 1
            if up == query[i].upper():
                matches += 1

        coverages.append(non_gap / L)
        identities.append(matches / L)

    warnings: list[str] = []
    if usable_hits < 10:
        warnings.append(f"usable_hits={usable_hits} (<10)")
    cov_stats = _percentiles(coverages, [25, 50, 75])
    id_stats = _percentiles(identities, [25, 50, 75])
    if cov_stats and float(cov_stats.get("p50", 0.0)) < 0.2:
        warnings.append(f"median_coverage={float(cov_stats.get('p50', 0.0)):.3f} (<0.2)")
    depth_stats = _percentiles([float(x) for x in depths], [10, 25, 50, 75, 90])
    if depth_stats and float(depth_stats.get("p50", 0.0)) < 10.0:
        warnings.append(f"median_depth={float(depth_stats.get('p50', 0.0)):.1f} (<10)")

    full_length_threshold = 0.7
    full_length_hits = sum(1 for cov in coverages if cov >= full_length_threshold)
    full_length_fraction = (full_length_hits / usable_hits) if usable_hits > 0 else 0.0
    if usable_hits > 0 and full_length_fraction < 0.05:
        warnings.append(
            f"full_length_fraction={full_length_fraction:.3f} (<0.05); consider msa_min_coverage>={full_length_threshold}"
        )

    return {
        "query_length": L,
        "total_hits": total_hits,
        "usable_hits": usable_hits,
        "length_mismatch_hits": length_mismatch_hits,
        "coverage": cov_stats,
        "identity_to_query": id_stats,
        "depth": depth_stats,
        "neff_like": (float(depth_stats.get("p50", 0.0)) if depth_stats else None),
        "full_length_coverage_threshold": full_length_threshold,
        "full_length_hits": full_length_hits,
        "full_length_fraction": full_length_fraction,
        "warnings": warnings,
    }


def filter_a3m(
    a3m_text: str,
    *,
    min_coverage: float = 0.0,
    min_identity: float = 0.0,
) -> tuple[str, dict[str, object]]:
    min_coverage = float(min_coverage)
    min_identity = float(min_identity)
    if min_coverage < 0.0 or min_coverage > 1.0:
        raise ValueError("min_coverage must be within [0, 1]")
    if min_identity < 0.0 or min_identity > 1.0:
        raise ValueError("min_identity must be within [0, 1]")

    records_raw = parse_fasta(a3m_text)
    records = _normalize_records(a3m_text)
    if not records:
        return a3m_text, {"kept_hits": 0, "dropped_hits": 0, "reason": "empty_a3m"}

    query = records[0].sequence
    L = len(query)
    if L <= 0:
        raise ValueError("A3M query sequence is empty")

    if min_coverage <= 0.0 and min_identity <= 0.0:
        return a3m_text, {"kept_hits": max(0, len(records) - 1), "dropped_hits": 0, "skipped": True}

    kept: list[FastaRecord] = [records_raw[0]]
    kept_hits = 0
    dropped_hits = 0
    dropped_length_mismatch = 0

    for raw_rec, norm_rec in zip(records_raw[1:], records[1:], strict=False):
        seq = norm_rec.sequence
        if len(seq) != L:
            dropped_hits += 1
            dropped_length_mismatch += 1
            continue

        non_gap = 0
        matches = 0
        for i, ch in enumerate(seq):
            if ch in {"-", "."}:
                continue
            non_gap += 1
            if ch.upper() == query[i].upper():
                matches += 1

        coverage = non_gap / L
        identity = matches / L

        if coverage < min_coverage or identity < min_identity:
            dropped_hits += 1
            continue

        kept.append(raw_rec)
        kept_hits += 1

    return (
        to_fasta(kept),
        {
            "min_coverage": min_coverage,
            "min_identity": min_identity,
            "kept_hits": kept_hits,
            "dropped_hits": dropped_hits,
            "dropped_length_mismatch": dropped_length_mismatch,
        },
    )


def weights_from_mmseqs_cluster_tsv(cluster_tsv: str) -> dict[str, float]:
    clusters: dict[str, set[str]] = {}
    for raw in str(cluster_tsv or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rep = parts[0].strip()
        member = parts[1].strip()
        if not rep or not member:
            continue
        members = clusters.setdefault(rep, set())
        members.add(member)

    weights: dict[str, float] = {}
    for rep, members in clusters.items():
        members.add(rep)
        size = len(members)
        if size <= 0:
            continue
        w = 1.0 / float(size)
        for member in members:
            prev = weights.get(member)
            if prev is None or w < prev:
                weights[member] = w
    return weights
