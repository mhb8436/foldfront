"""Check what a model returned against the file it returned.

Two stages report a number that can be recomputed from their own output, and
until now this platform recorded the number and never looked.

**MSA.** The alignment comes back as A3M text. Its depth, its coverage, and
which positions are conserved enough to fix are all derivable from that text,
and the original derives them - `bio/a3m.py` has `msa_quality`,
`conservation_scores` and `fixed_positions`. Those are the functions, called
here; the platform computes none of it. This is SFR-003: the conservation
tiers and the fixed-residue rule, actually computed rather than passed
through as a parameter nobody applied.

**Structure prediction.** AF2 reports a pLDDT and this recomputes it from the
B-factor column of the PDB it returned, which is where a predictor writes
per-residue confidence. It also measures the RMSD to the backbone the design
started from, using the original's Kabsch superposition in `bio/pdb.py`. This
is SFR-007's second check: not "what did the model say" but "does the file
agree with what it said".

The point of recomputing is disagreement. A reported pLDDT that the returned
structure does not support is the kind of thing that survives a whole
campaign unnoticed, because every screen downstream shows the reported
number. When the two differ by more than a rounding error this says so, and
the quality panel raises it.

Nothing here fails a stage. A verification that cannot run - no reference
structure, an unparseable file - records that it could not run. Failing a
stage over a check that is itself uncertain would throw away work the model
actually did.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#  Below this the recomputed and reported pLDDT are treated as the same
#  number. Predictors round, and the B-factor column carries two decimals.
PLDDT_TOLERANCE = 1.0


class Unavailable(RuntimeError):
    """The original's bio functions could not be loaded."""


# ---------------------------------------------------------------- MSA


def read_msa(a3m_text: str, *, tiers: list[float] | None = None,
             mode: str = "quantile") -> dict[str, Any]:
    """Depth, coverage and conserved positions, from the alignment itself.

    `tiers` are fractions of the sequence to hold fixed - 0.1 meaning the
    most conserved tenth. They come from the run request, because how much
    of a protein to leave alone is the designer's decision and not ours.
    """
    if not str(a3m_text or "").strip():
        return {}
    try:
        from pipeline_mcp.bio.a3m import compute_conservation, msa_quality
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise Unavailable(str(exc)) from exc

    out: dict[str, Any] = {}
    try:
        quality = msa_quality(a3m_text)
    except Exception as exc:
        log.warning("MSA 품질을 읽지 못했다: %s", exc)
        return {"msa_unreadable": str(exc)}

    #  Named as the original names them, so the thresholds in signals.py -
    #  which are the original's - read the values they were written against.
    for key in ("total_hits", "usable_hits", "length_mismatch_hits"):
        if key in quality:
            out[key] = quality[key]
    coverage = quality.get("coverage")
    if isinstance(coverage, dict):
        #  The original reports a distribution; the median is the one a
        #  threshold is written against.
        median = coverage.get("p50") or coverage.get("mean")
        if isinstance(median, (int, float)):
            out["median_coverage"] = round(float(median), 4)
        out["coverage_stats"] = coverage
    if isinstance(quality.get("identity"), dict):
        out["identity_stats"] = quality["identity"]

    #  Depth and coverage under our own names too, so a branch condition
    #  written against a run of ours keeps working.
    if "usable_hits" in out:
        out["depth"] = out["usable_hits"]
    if "median_coverage" in out:
        out["coverage"] = out["median_coverage"]

    wanted = [float(t) for t in (tiers or []) if isinstance(t, (int, float))]
    if wanted:
        try:
            conservation = compute_conservation(a3m_text, tiers=wanted, mode=mode)
        except Exception as exc:
            log.warning("보존도를 계산하지 못했다: %s", exc)
            out["conservation_unavailable"] = str(exc)
            return out
        out["query_length"] = conservation.query_length
        #  Counts for the screens and the thresholds; the positions
        #  themselves for the design stage that has to honour them.
        out["fixed_positions"] = {
            str(tier): len(positions)
            for tier, positions in conservation.fixed_positions_by_tier.items()
        }
        out["fixed_positions_by_tier"] = {
            str(tier): positions
            for tier, positions in conservation.fixed_positions_by_tier.items()
        }
    return out


# ---------------------------------------------------------------- structure


def mean_plddt(pdb_text: str) -> float | None:
    """Mean per-residue confidence, read off the CA atoms.

    A structure predictor writes pLDDT into the B-factor column. Averaging
    over CA atoms rather than all atoms because the value is per residue and
    every atom of a residue carries the same one - counting them all would
    weight large residues more, which the number does not mean.
    """
    total, count = 0.0, 0
    for line in str(pdb_text or "").splitlines():
        if not line.startswith("ATOM"):
            continue
        #  Fixed columns, as the format defines them. Whitespace splitting
        #  breaks on PDBs where adjacent fields run together.
        if line[12:16].strip() != "CA":
            continue
        try:
            total += float(line[60:66])
            count += 1
        except ValueError:
            continue
    if count == 0:
        return None
    return round(total / count, 2)


def check_structure(
    pdb_text: str,
    *,
    reported_plddt: float | None = None,
    reference_pdb: str | None = None,
    chains: list[str] | None = None,
) -> dict[str, Any]:
    """Recompute what the predictor reported, from what it returned.

    Returns what it could measure and why it could not measure the rest. An
    empty answer is not a failure; it means there was nothing to check
    against, which is itself worth recording.
    """
    out: dict[str, Any] = {}
    if not str(pdb_text or "").strip():
        return {"verified": False, "reason": "구조 파일이 비어 있습니다"}

    measured = mean_plddt(pdb_text)
    if measured is None:
        out["plddt_measured"] = None
        out["plddt_reason"] = "CA 원자의 B-factor 를 읽지 못했습니다"
    else:
        out["plddt_measured"] = measured
        if isinstance(reported_plddt, (int, float)):
            gap = abs(float(reported_plddt) - measured)
            out["plddt_reported"] = round(float(reported_plddt), 2)
            out["plddt_gap"] = round(gap, 2)
            #  The one finding worth having. A reported number the returned
            #  file does not support travels through every screen downstream
            #  unless it is caught right here.
            out["plddt_agrees"] = gap <= PLDDT_TOLERANCE

    if reference_pdb and str(reference_pdb).strip():
        try:
            from pipeline_mcp.bio.pdb import ca_rmsd
        except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
            raise Unavailable(str(exc)) from exc
        try:
            rmsd = ca_rmsd(reference_pdb, pdb_text, chains=chains)
        except Exception as exc:
            log.warning("RMSD 를 계산하지 못했다: %s", exc)
            out["rmsd_reason"] = str(exc)
            rmsd = None
        if rmsd is None:
            out.setdefault("rmsd_reason", "겹칠 수 있는 CA 원자가 모자랍니다")
        else:
            out["rmsd_measured"] = round(float(rmsd), 3)
    else:
        out["rmsd_reason"] = "견줄 기준 구조가 없습니다"

    out["verified"] = "plddt_measured" in out or "rmsd_measured" in out
    return out
