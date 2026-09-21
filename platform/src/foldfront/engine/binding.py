"""Binding prediction: the interface, a score for a pose, and a ranking.

The workflow template for binding has been registered since the DAG engine
went in - ten nodes, docking and complex prediction, a branch and a parallel
split. What it never had was anything to do with the result. A pose came
back and nothing read it.

Three things are needed to turn a pose into a decision, and this is where
they live.

**The interface.** Which residues of the protein are in contact with the
ligand. Computed by the original's `ligand_proximity_mask`, at the original's
own 6.0 A - inherited, not chosen here.

**A score.** DiffDock reports a confidence per pose, and that confidence is
about the pose being right, not about the binding being good. A pose the
model is sure of, that buries nothing, is a confident prediction of a weak
interaction. So the score combines the two.

**A ranking.** Poses sorted by that score, with the reason each one placed
where it did kept alongside, because a ranking whose reasoning is not
visible is one nobody will act on.

---

**On the numbers.** The cutoffs below are conventional defaults with their
sources named, and they are parameters rather than constants for exactly the
reason that they are not this platform's to settle: which of pose confidence
and interface size should dominate is a question about the campaign and the
target, and a different answer is not a bug. They are stated here so that a
reader can see what was assumed, and changed in the request when it is wrong.

What this module will not do is turn a score into a binding affinity.
DiffDock does not predict one, nothing else in the chain does either, and a
number in kcal/mol derived from a docking confidence would be an invention
with units on it. The score is a ranking key and is named as one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#  The original's own default in `ligand_proximity_mask`. Inherited rather
#  than picked, which is why it is this and not the 4.0 A a contact map
#  would use: changing it would put this platform's interface count at odds
#  with the one the original's masking step produces for the same complex.
INTERFACE_ANGSTROM = 6.0

#  DiffDock's confidence is a log-odds-like value, and the bands are the
#  ones its authors describe: above 0 the pose is likely right, below -1.5
#  it should not be relied on. Not a binding strength - a pose being right
#  and a ligand binding well are different claims.
CONFIDENCE_GOOD = 0.0
CONFIDENCE_POOR = -1.5

#  An interface this small is a pose touching the surface rather than
#  sitting in a pocket. Twelve residues is roughly what a small-molecule
#  site contacts at 6 A; below about half that there is no pocket.
INTERFACE_TYPICAL = 12
INTERFACE_MINIMAL = 5

#  How the two are weighted against each other. Confidence leads because a
#  pose that is wrong makes its interface meaningless, so there is an order
#  of dependence between them rather than a free choice.
DEFAULT_WEIGHTS: dict[str, float] = {"confidence": 0.6, "interface": 0.4}


class Unavailable(RuntimeError):
    """The original's structure functions could not be loaded."""


@dataclass
class Interface:
    """Which residues touch the ligand, and how many."""

    residues_by_chain: dict[str, list[int]] = field(default_factory=dict)
    distance_angstrom: float = INTERFACE_ANGSTROM

    @property
    def count(self) -> int:
        return sum(len(v) for v in self.residues_by_chain.values())

    @property
    def chains(self) -> list[str]:
        return sorted(self.residues_by_chain)

    def as_dict(self) -> dict[str, Any]:
        return {
            "residues_by_chain": {k: list(v) for k, v in self.residues_by_chain.items()},
            "residue_count": self.count,
            "chains": self.chains,
            "distance_angstrom": self.distance_angstrom,
        }


def read_interface(
    complex_pdb: str,
    *,
    distance_angstrom: float = INTERFACE_ANGSTROM,
    ligand_resnames: list[str] | None = None,
    chains: list[str] | None = None,
) -> Interface:
    """Residues within reach of the ligand, by the original's own measure."""
    if not str(complex_pdb or "").strip():
        return Interface(distance_angstrom=distance_angstrom)
    try:
        from pipeline_mcp.bio.pdb import ligand_proximity_mask
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise Unavailable(str(exc)) from exc

    try:
        mask = ligand_proximity_mask(
            complex_pdb,
            chains=chains,
            distance_angstrom=float(distance_angstrom),
            ligand_resnames=ligand_resnames,
        )
    except Exception as exc:
        log.warning("결합 인터페이스를 읽지 못했다: %s", exc)
        return Interface(distance_angstrom=distance_angstrom)
    return Interface(residues_by_chain=dict(mask), distance_angstrom=float(distance_angstrom))


def _confidence_score(confidence: float | None) -> float | None:
    """DiffDock's confidence onto 0..1, by its authors' own bands."""
    if confidence is None:
        return None
    if confidence >= CONFIDENCE_GOOD:
        #  Above the band, more confidence adds little: the pose is already
        #  judged likely right, and the question moves to the interface.
        return min(1.0, 0.75 + 0.25 * min(1.0, float(confidence) / 2.0))
    if confidence <= CONFIDENCE_POOR:
        return 0.0
    #  Linear across the band it is actually uncertain in.
    span = CONFIDENCE_GOOD - CONFIDENCE_POOR
    return round(0.75 * (float(confidence) - CONFIDENCE_POOR) / span, 4)


def _interface_score(count: int) -> float:
    """Interface size onto 0..1, flat once there is a pocket."""
    if count <= INTERFACE_MINIMAL:
        return 0.0
    if count >= INTERFACE_TYPICAL:
        return 1.0
    return round(
        (count - INTERFACE_MINIMAL) / float(INTERFACE_TYPICAL - INTERFACE_MINIMAL), 4
    )


def score_pose(
    *,
    confidence: float | None,
    interface: Interface,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """A ranking key for one pose, with the parts that made it.

    The parts are returned, not just the total. A ranking a person cannot
    take apart is one they have to trust whole, and they will not.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_weight = sum(max(0.0, float(v)) for v in w.values())

    parts: dict[str, float | None] = {
        "confidence": _confidence_score(confidence),
        "interface": _interface_score(interface.count),
    }

    #  A missing part drops out of both the sum and the divisor rather than
    #  scoring zero. Scoring it zero would rank a pose with no confidence
    #  reported below one the model actively doubted.
    used = {k: v for k, v in parts.items() if isinstance(v, (int, float))}
    divisor = sum(max(0.0, float(w.get(k, 0.0))) for k in used)
    score = (
        round(sum(float(v) * max(0.0, float(w.get(k, 0.0))) for k, v in used.items()) / divisor, 4)
        if divisor > 0
        else None
    )

    return {
        "score": score,
        "components": parts,
        "weights": {k: float(v) for k, v in w.items()},
        "weight_total": total_weight,
        "scored_on": sorted(used),
        #  Named plainly. It orders poses; it is not an affinity, and
        #  nothing in this chain predicts one.
        "score_is": "ranking key, not a binding affinity",
        "confidence": confidence,
        "interface_residues": interface.count,
    }


def rank_poses(
    poses: list[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    distance_angstrom: float = INTERFACE_ANGSTROM,
    ligand_resnames: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score every pose and order them.

    A pose is a dict as the docking reply carries it: a confidence, and the
    complex it produced. Anything already on it is kept, so a caller reading
    the ranking still has what the model said.
    """
    scored: list[dict[str, Any]] = []
    for i, pose in enumerate(poses or []):
        if not isinstance(pose, dict):
            continue
        confidence = pose.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = None
        complex_pdb = str(
            pose.get("complex_pdb") or pose.get("complex") or pose.get("pdb") or ""
        )
        interface = read_interface(
            complex_pdb,
            distance_angstrom=distance_angstrom,
            ligand_resnames=ligand_resnames,
        )
        scored.append({
            **pose,
            **score_pose(confidence=confidence, interface=interface, weights=weights),
            "interface": interface.as_dict(),
            #  Where it arrived in, so a reader can see that the ranking
            #  moved it and by how much.
            "reported_rank": pose.get("rank", i + 1),
        })

    #  Unscorable poses go last rather than being dropped: a pose that came
    #  back is a fact, and hiding it would make the count disagree with
    #  what the endpoint returned.
    scored.sort(key=lambda p: (p["score"] is not None, p["score"] or 0.0), reverse=True)
    for rank, pose in enumerate(scored, start=1):
        pose["rank"] = rank
    return scored


def read_docking(reply: dict[str, Any]) -> dict[str, Any]:
    """Metrics for a docking stage. Called from results.interpret.

    Reads whatever shape the reply carries the poses in, because DiffDock is
    called through several wrappers and none of them agree on the key.
    """
    poses = None
    for key in ("poses", "results", "predictions", "ranked"):
        value = reply.get(key)
        if isinstance(value, list) and value:
            poses = value
            break
    if poses is None:
        #  A single pose, which is what the original's own runner keeps.
        single = str(reply.get("complex_pdb") or reply.get("complex") or "")
        if not single:
            return {}
        poses = [{"confidence": reply.get("confidence"), "complex_pdb": single}]

    weights = reply.get("binding_weights") if isinstance(reply.get("binding_weights"), dict) else None
    distance = reply.get("interface_angstrom")
    ranked = rank_poses(
        poses,
        weights=weights,
        distance_angstrom=float(distance) if isinstance(distance, (int, float)) else INTERFACE_ANGSTROM,
        ligand_resnames=reply.get("ligand_resnames") if isinstance(reply.get("ligand_resnames"), list) else None,
    )
    if not ranked:
        return {}

    best = ranked[0]
    return {
        "poses": len(ranked),
        "ranked_poses": ranked,
        "best_score": best.get("score"),
        "best_confidence": best.get("confidence"),
        "best_interface_residues": best.get("interface_residues"),
        #  For a branch condition: whether anything here is worth carrying on
        #  with. Both halves have to hold - a confident pose touching nothing
        #  is not a hit, and neither is a large interface nobody believes.
        "acceptable_poses": sum(
            1 for p in ranked
            if isinstance(p.get("confidence"), (int, float))
            and float(p["confidence"]) >= CONFIDENCE_POOR
            and int(p.get("interface_residues") or 0) > INTERFACE_MINIMAL
        ),
    }
