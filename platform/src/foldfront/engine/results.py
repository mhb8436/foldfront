"""Turn a model's reply into the metrics a run records.

Adapters return whatever the endpoint sent. That is the right thing for them to
do - they should not know what a model means - but a branch condition reads
values by path, and `soluprot.pass_rate > 0.3` cannot be answered from a list
of per-sequence scores.

This is where a reply becomes metrics. It is deliberately the reverse of
payloads.py: that module knows what each model expects, this one knows what
each model returns, and neither knows how the call was made.

A model with no interpreter keeps its reply as it came. A custom model has a
shape of its own, and a wrong guess about it would be worse than passing it
through.
"""

from __future__ import annotations

from typing import Any, Callable

#  Below this a design is treated as unlikely to stay soluble in E. coli. It is
#  the threshold the original uses, and the branch conditions in the shipped
#  templates are written against the pass rate it produces.
SOLUBILITY_THRESHOLD = 0.5


def _soluprot(reply: dict[str, Any]) -> dict[str, Any]:
    """Per-sequence scores become a pass rate the triage branch can read."""
    results = reply.get("results")
    if not isinstance(results, list) or not results:
        return {"passed": 0, "pass_rate": 0.0, "scored": 0}

    scores = [
        float(r.get("score") or 0.0)
        for r in results
        if isinstance(r, dict)
    ]
    passed = sum(1 for s in scores if s >= SOLUBILITY_THRESHOLD)
    return {
        "scored": len(scores),
        "passed": passed,
        "pass_rate": round(passed / len(scores), 3) if scores else 0.0,
        "mean_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        #  Which sequences survived, so the next stage has something to act on
        "passed_ids": [
            str(r.get("id"))
            for r in results
            if isinstance(r, dict) and float(r.get("score") or 0.0) >= SOLUBILITY_THRESHOLD
        ],
    }


def _count(key: str, metric: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """For replies that are a list under a known key."""

    def read(reply: dict[str, Any]) -> dict[str, Any]:
        items = reply.get(key)
        return {metric: len(items)} if isinstance(items, list) else {}

    return read


def _msa(reply: dict[str, Any]) -> dict[str, Any]:
    """Depth, coverage and conserved positions, computed from the alignment.

    The endpoint returns A3M text and, usually, a depth it counted itself.
    Everything here is recomputed from the text with the original's own
    functions - which is the point: the conservation tiers and the fixed
    residues were a parameter nobody applied until this ran.
    """
    from foldfront.engine.verify import Unavailable, read_msa

    a3m = _first_text(reply, "a3m", "msa_a3m", "alignment", "msa")
    if not a3m:
        return {}
    tiers = reply.get("conservation_tiers")
    tiers = [float(t) for t in tiers] if isinstance(tiers, list) else []
    try:
        return read_msa(a3m, tiers=tiers)
    except Unavailable:  # pragma: no cover - 원본을 뗀 구성
        return {}


def _structure(reply: dict[str, Any]) -> dict[str, Any]:
    """Check the predictor's own numbers against the structure it returned.

    Reported and measured are both kept, under names that say which is
    which. Everything downstream reads `plddt`, so that stays the reported
    value where there is one - replacing it silently would mean a screen
    showing a number no one can find in the model's reply.
    """
    from foldfront.engine.verify import Unavailable, check_structure

    pdb = _first_text(reply, "pdb", "ranked_0_pdb", "structure", "unrelaxed_pdb")
    if not pdb:
        return {}
    reported = reply.get("plddt")
    if not isinstance(reported, (int, float)):
        reported = reply.get("mean_plddt")
    try:
        checked = check_structure(
            pdb,
            reported_plddt=reported if isinstance(reported, (int, float)) else None,
            reference_pdb=_first_text(reply, "reference_pdb", "backbone_pdb", "input_pdb"),
        )
    except Unavailable:  # pragma: no cover - 원본을 뗀 구성
        return {}

    out = dict(checked)
    #  No reported value: the measured one is the only one there is.
    if reported is None and isinstance(checked.get("plddt_measured"), (int, float)):
        out["plddt"] = checked["plddt_measured"]
    if isinstance(checked.get("rmsd_measured"), (int, float)) and "rmsd" not in reply:
        out["rmsd"] = checked["rmsd_measured"]
    return out


def _first_text(reply: dict[str, Any], *keys: str) -> str | None:
    """The first of these keys holding text worth parsing."""
    for key in keys:
        value = reply.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


INTERPRETERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "soluprot": _soluprot,
    "rfd3": _count("backbones", "backbones"),
    "bioemu": _count("structures", "structures"),
    "design": _count("sequences", "sequences"),
    "proteinmpnn": _count("sequences", "sequences"),
    "msa": _msa,
    "mmseqs": _msa,
    "af2": _structure,
    "colabfold": _structure,
    "esmfold": _structure,
}


def interpret(model_id: str, reply: dict[str, Any]) -> dict[str, Any]:
    """Metrics for a run, merged over the reply rather than replacing it.

    The raw reply is kept: a later stage may need a field no metric covers, and
    discarding it here would mean the only copy was gone.
    """
    read = INTERPRETERS.get(model_id)
    if read is None:
        return dict(reply)
    try:
        return {**reply, **read(reply)}
    except (TypeError, ValueError, KeyError):
        #  A reply that does not fit is not a reason to fail a stage that ran.
        return dict(reply)


def known_models() -> tuple[str, ...]:
    return tuple(sorted(INTERPRETERS))
