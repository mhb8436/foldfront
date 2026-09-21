"""Turn a sentence into a workflow draft, using the original's router.

`pipeline_mcp/router.py:plan_from_prompt` reads a prompt and produces three
things: the request parameters it could infer, the inputs it still needs, and
the questions worth asking before spending GPU time. That routing is the
original's and is not reimplemented here.

What this adds is the step after. The original's answer is shaped for a fixed
stage chain - `start_from`, `stop_after` and a flat request - while this
platform runs a DAG. So the plan is turned into nodes and edges that the
studio can open and a person can edit before anything runs.

    prompt ──router.plan_from_prompt──> routed_request · missing · questions
                                              │
                                              └──> nodes · edges (a draft)

**A draft is not a run.** Nothing here starts anything. A plan with required
inputs still missing is returned as a plan with those inputs missing, and the
console says so, because the alternative - filling them with defaults and
starting - spends money on a guess.

**The router reads English model names.** `rfd3`, `bioemu`, `diffdock` are
what it matches on, and they are the model names in any language. A prompt
with none of them routes to the default chain, which is reported as such
rather than dressed up as a decision.
"""

from __future__ import annotations

import logging
from typing import Any

from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode
from foldfront.engine.dag import BUILTIN_STAGE_CHAIN

log = logging.getLogger(__name__)


class PlannerUnavailable(RuntimeError):
    """The original's router could not be loaded."""


#  The original's own word for the last stage of the fixed chain. Its
#  question offers `wt_diff` and its default is `novelty`; both name the
#  same stage, and the chain here spells it `novelty`.
_STAGE_ALIASES = {"wt_diff": "novelty", "wtdiff": "novelty"}


def _stage(name: Any) -> str | None:
    raw = str(name or "").strip().lower()
    raw = _STAGE_ALIASES.get(raw, raw)
    return raw if raw in BUILTIN_STAGE_CHAIN else None


def plan(prompt: str, **inputs: Any) -> dict[str, Any]:
    """Route a prompt and draft a workflow from the result.

    `inputs` are passed to the original untouched - target_fasta, target_pdb,
    rfd3_input_pdb and the rest - because whether an input is missing is its
    question to answer, not ours.
    """
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("계획을 세울 문장이 비어 있습니다")

    try:
        from pipeline_mcp.router import plan_from_prompt
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise PlannerUnavailable(str(exc)) from exc

    accepted = {
        k: v for k, v in inputs.items()
        if k in {"target_fasta", "target_pdb", "rfd3_input_pdb", "rfd3_contig",
                 "diffdock_ligand_smiles", "diffdock_ligand_sdf"}
        and v is not None
    }
    routed = plan_from_prompt(prompt=text, **accepted)

    request = dict(routed.get("routed_request") or {})
    stages = _stages_for(request, prompt=text)
    draft = _draft(stages, request)

    missing = list(routed.get("missing") or [])
    return {
        "prompt": text,
        #  The original's three answers, unchanged.
        "routed_request": request,
        "missing": missing,
        "questions": list(routed.get("questions") or []),
        "errors": list(routed.get("errors") or []),
        #  What we made of them.
        "stages": stages,
        "workflow": draft.model_dump(),
        "ready": not missing,
        #  Say when nothing in the sentence steered the plan, so a default
        #  chain is not read as a decision the router made.
        "defaulted": not request,
        "note": _note(request, missing),
    }


def _stages_for(request: dict[str, Any], *, prompt: str) -> list[str]:
    """Which stages the plan covers, narrowed by where to start and stop."""
    chain = list(BUILTIN_STAGE_CHAIN)

    start = _stage(request.get("start_from")) or chain[0]
    stop = _stage(request.get("stop_after")) or chain[-1]
    lo, hi = chain.index(start), chain.index(stop)
    if lo > hi:
        #  A prompt asking to start after it stops is a prompt we cannot
        #  honour. Keeping the wider range lets the person see both ends
        #  and fix it, rather than getting an empty plan back.
        lo, hi = min(lo, hi), max(lo, hi)
    selected = chain[lo : hi + 1]

    #  The original turns these on in the request rather than in the chain.
    if request.get("bioemu_use") is False and "bioemu" in selected:
        selected.remove("bioemu")
    if "diffdock" in prompt.lower() and "diffdock" not in selected:
        selected.append("diffdock")
    return selected


def _draft(stages: list[str], request: dict[str, Any]) -> Workflow:
    """A straight chain of the selected stages, carrying the routed parameters.

    Straight, not branched: the router has no notion of a condition, and a
    branch invented here would be this module guessing at a triage rule the
    person never asked for. They add one in the studio, where the condition
    is visible and editable.
    """
    nodes = [
        WorkflowNode(
            node_id=name,
            kind=NodeKind.MODEL,
            model_id=name,
            label=name,
            params=_params_for(name, request),
            position={"x": float(i * 180), "y": 0.0},
        )
        for i, name in enumerate(stages)
    ]
    edges = [WorkflowEdge(source=a, target=b) for a, b in zip(stages, stages[1:])]
    return Workflow(
        workflow_id="",  # unsaved: the console names it when it is saved
        name="계획 초안",
        description="자연어 요청에서 만든 초안입니다. 저장하기 전에 손보십시오.",
        nodes=nodes,
        edges=edges,
        is_template=False,
    )


#  Which routed keys belong to which stage. Anything not listed stays on the
#  run request, where the engine already passes it to every node.
_STAGE_PARAMS: dict[str, tuple[str, ...]] = {
    "msa": ("mmseqs_max_seqs", "msa_mode", "conservation_tiers"),
    "rfd3": ("rfd3_contig", "rfd3_num_designs", "rfd3_input_pdb"),
    "bioemu": ("bioemu_use", "bioemu_num_samples"),
    "design": ("num_seq_per_tier", "sampling_temp", "design_chains"),
    "soluprot": ("soluprot_cutoff",),
    "af2": ("af2_provider", "af2_max_candidates_per_tier"),
    "diffdock": ("diffdock_ligand_smiles", "diffdock_ligand_sdf"),
}


def _params_for(stage: str, request: dict[str, Any]) -> dict[str, Any]:
    return {
        key: request[key]
        for key in _STAGE_PARAMS.get(stage, ())
        if key in request
    }


def _note(request: dict[str, Any], missing: list[str]) -> str:
    if missing:
        return (
            "필요한 입력이 아직 없습니다. 아래 질문에 답하고 다시 계획하거나, "
            "스튜디오에서 직접 채우십시오. 이대로는 실행하지 않습니다."
        )
    if not request:
        return (
            "문장에서 읽어낸 조건이 없어 정형 단계 체인을 그대로 냈습니다. "
            "모델 이름(rfd3 · bioemu · diffdock)을 적으면 그에 맞춰 달라집니다."
        )
    return "초안을 만들었습니다. 스튜디오에서 확인하고 저장하십시오."
