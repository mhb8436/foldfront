from __future__ import annotations

import math
import re
from typing import Iterable

from ..models import SequenceRecord


_SEQ_RE = re.compile(r"[^A-Za-z]+")

_PKA = {
    "n_term": 9.69,
    "c_term": 2.34,
    "C": 8.33,
    "D": 3.86,
    "E": 4.25,
    "H": 6.0,
    "K": 10.5,
    "R": 12.5,
    "Y": 10.07,
}


def _clean_sequence(seq: str) -> str:
    return _SEQ_RE.sub("", str(seq or "")).upper()


def _count_residues(seq: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for aa in _clean_sequence(seq):
        counts[aa] = counts.get(aa, 0) + 1
    return counts


def _charge_at_ph(counts: dict[str, int], ph: float) -> float:
    n_term = 1.0 / (1.0 + 10 ** (ph - _PKA["n_term"]))
    c_term = 1.0 / (1.0 + 10 ** (_PKA["c_term"] - ph))
    pos = n_term
    neg = c_term

    pos += counts.get("K", 0) / (1.0 + 10 ** (ph - _PKA["K"]))
    pos += counts.get("R", 0) / (1.0 + 10 ** (ph - _PKA["R"]))
    pos += counts.get("H", 0) / (1.0 + 10 ** (ph - _PKA["H"]))

    neg += counts.get("D", 0) / (1.0 + 10 ** (_PKA["D"] - ph))
    neg += counts.get("E", 0) / (1.0 + 10 ** (_PKA["E"] - ph))
    neg += counts.get("C", 0) / (1.0 + 10 ** (_PKA["C"] - ph))
    neg += counts.get("Y", 0) / (1.0 + 10 ** (_PKA["Y"] - ph))

    return pos - neg


def isoelectric_point(sequence: str, *, ph_min: float = 0.0, ph_max: float = 14.0) -> float:
    seq = _clean_sequence(sequence)
    if not seq:
        return 7.0
    counts = _count_residues(seq)
    lo = float(ph_min)
    hi = float(ph_max)
    for _ in range(32):
        mid = (lo + hi) / 2.0
        charge = _charge_at_ph(counts, mid)
        if charge > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def filter_records_by_pi(
    records: Iterable[SequenceRecord],
    *,
    pi_min: float | None = None,
    pi_max: float | None = None,
) -> tuple[list[SequenceRecord], dict[str, float]]:
    scores: dict[str, float] = {}
    passed: list[SequenceRecord] = []
    for rec in records:
        pi_val = isoelectric_point(rec.sequence)
        scores[str(rec.id)] = pi_val
        if pi_min is not None and pi_val < float(pi_min):
            continue
        if pi_max is not None and pi_val > float(pi_max):
            continue
        passed.append(rec)
    return passed, scores
