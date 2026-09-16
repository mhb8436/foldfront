from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlignmentResult:
    query_len: int
    target_len: int
    aligned_pairs: int
    matches: int
    mismatches: int
    gaps_in_target: int
    gaps_in_query: int
    pairwise_identity: float
    query_identity: float
    target_identity: float
    coverage_query: float
    coverage_target: float
    mapping_query_to_target: list[int | None]


def global_alignment_mapping(
    query: str,
    target: str,
    *,
    match_score: int = 2,
    mismatch_score: int = -1,
    gap_score: int = -2,
) -> AlignmentResult:
    query = str(query or "")
    target = str(target or "")
    n = len(query)
    m = len(target)

    if n <= 0:
        return AlignmentResult(
            query_len=0,
            target_len=m,
            aligned_pairs=0,
            matches=0,
            mismatches=0,
            gaps_in_target=0,
            gaps_in_query=m,
            pairwise_identity=0.0,
            query_identity=0.0,
            target_identity=0.0,
            coverage_query=0.0,
            coverage_target=0.0,
            mapping_query_to_target=[],
        )
    if m <= 0:
        return AlignmentResult(
            query_len=n,
            target_len=0,
            aligned_pairs=0,
            matches=0,
            mismatches=0,
            gaps_in_target=n,
            gaps_in_query=0,
            pairwise_identity=0.0,
            query_identity=0.0,
            target_identity=0.0,
            coverage_query=0.0,
            coverage_target=0.0,
            mapping_query_to_target=[None for _ in range(n)],
        )

    if query == target:
        return AlignmentResult(
            query_len=n,
            target_len=m,
            aligned_pairs=n,
            matches=n,
            mismatches=0,
            gaps_in_target=0,
            gaps_in_query=0,
            pairwise_identity=1.0,
            query_identity=1.0,
            target_identity=1.0,
            coverage_query=1.0,
            coverage_target=1.0,
            mapping_query_to_target=list(range(1, n + 1)),
        )

    stride = m + 1
    ptr = bytearray((n + 1) * stride)

    # First row: only LEFT moves.
    for j in range(1, stride):
        ptr[j] = 2

    dp_prev = [gap_score * j for j in range(stride)]

    for i in range(1, n + 1):
        dp_cur = [0] * stride
        dp_cur[0] = gap_score * i
        ptr[i * stride] = 1

        qch = query[i - 1]
        base = i * stride
        diag_prev = dp_prev[0]
        for j in range(1, stride):
            t_prev = target[j - 1]
            diag = diag_prev + (match_score if qch == t_prev else mismatch_score)
            up = dp_prev[j] + gap_score
            left = dp_cur[j - 1] + gap_score

            best = diag
            direction = 0
            if up > best:
                best = up
                direction = 1
            if left > best:
                best = left
                direction = 2

            dp_cur[j] = best
            ptr[base + j] = direction
            diag_prev = dp_prev[j]

        dp_prev = dp_cur

    mapping: list[int | None] = [None for _ in range(n)]
    matches = 0
    mismatches = 0
    aligned_pairs = 0
    gaps_in_target = 0
    gaps_in_query = 0

    i = n
    j = m
    while i > 0 or j > 0:
        direction = ptr[i * stride + j]
        if direction == 0:
            aligned_pairs += 1
            if query[i - 1] == target[j - 1]:
                matches += 1
            else:
                mismatches += 1
            mapping[i - 1] = j
            i -= 1
            j -= 1
        elif direction == 1:
            gaps_in_target += 1
            mapping[i - 1] = None
            i -= 1
        else:
            gaps_in_query += 1
            j -= 1

    pairwise_identity = (matches / aligned_pairs) if aligned_pairs else 0.0
    query_identity = matches / n if n else 0.0
    target_identity = matches / m if m else 0.0
    coverage_query = aligned_pairs / n if n else 0.0
    coverage_target = aligned_pairs / m if m else 0.0

    return AlignmentResult(
        query_len=n,
        target_len=m,
        aligned_pairs=aligned_pairs,
        matches=matches,
        mismatches=mismatches,
        gaps_in_target=gaps_in_target,
        gaps_in_query=gaps_in_query,
        pairwise_identity=pairwise_identity,
        query_identity=query_identity,
        target_identity=target_identity,
        coverage_query=coverage_query,
        coverage_target=coverage_target,
        mapping_query_to_target=mapping,
    )
