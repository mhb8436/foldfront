"""Analysis read paths, answered by the original's own functions.

The weighted ranking, the WT difference, the comparison metrics and the
completeness flags are all the original's. This module does not recompute any
of them: it projects the run so the original can read it, calls the tool, and
returns what came back.

    GET /runs/{run_id}/hit-list              pipeline.get_hit_list
    GET /runs/{run_id}/compare               pipeline.compare_runs

Why not let the console call /mcp directly: a JSON-RPC envelope around a JSON
string is a poor thing to render from, and the run-scope check would then be
written twice. These are ordinary REST paths with the same check on them.

Nothing here invents a number. Where the original has no data - a run whose
stages never produced tiers - the answer says so and the rows are empty,
which is the truth about that run rather than a table of plausible values.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from foldfront.core.auth import CurrentIdentity, may_see_run, require
from foldfront.core.config import get_settings
from foldfront.core.errors import ApiError, E
from foldfront.db.models import Role
from foldfront.db.repositories import Repos
from foldfront.engine.legacy_view import LegacyProjector

log = logging.getLogger(__name__)

router = APIRouter(tags=["Analysis"])


def repos() -> Repos:
    return Repos()


async def _prepared(run_id: str, identity: CurrentIdentity) -> Any:
    """Check the caller may see the run, project it, and hand back a dispatcher.

    Raises rather than returns on refusal, so a caller cannot forget to look.
    """
    r = repos()
    run = await r.runs.get(run_id)
    if run is None:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id)
    if not await may_see_run(identity, run_id):
        raise ApiError(E.AUTH_FORBIDDEN)

    await LegacyProjector(r).project(run_id)

    try:
        from pipeline_mcp.pipeline import PipelineRunner
        from pipeline_mcp.tools import ToolDispatcher
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise ApiError(E.UPSTREAM_UNAVAILABLE, reason=str(exc)) from exc

    root = str(Path(get_settings().output_root).resolve())
    return ToolDispatcher(runner=PipelineRunner(output_root=root))


def _call(dispatcher: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = dispatcher.call_tool(tool, args)
    except ValueError as exc:
        #  The original raises ValueError for a run it cannot read and for an
        #  argument it will not accept. Both are the caller's answer.
        raise ApiError(E.UPSTREAM_REFUSED, tool=tool, reason=str(exc)) from exc
    except Exception as exc:
        log.warning("원본 도구 %s 가 실패했다: %s", tool, exc)
        raise ApiError(E.UPSTREAM_REFUSED, tool=tool, reason=str(exc)) from exc
    return result if isinstance(result, dict) else {"result": result}


@router.get("/runs/{run_id}/hit-list", summary="Ranked design candidates, with the WT difference")
async def hit_list(
    run_id: str,
    identity: CurrentIdentity,
    limit: int = Query(default=120, ge=1, le=1000),
    min_score: float = Query(default=0.0),
    rmsd_ref: float = Query(default=5.0, gt=0),
    soluprot: float = Query(default=0.4, ge=0, le=1, description="Weight"),
    plddt: float = Query(default=0.3, ge=0, le=1, description="Weight"),
    rmsd: float = Query(default=0.2, ge=0, le=1, description="Weight"),
    novelty: float = Query(default=0.1, ge=0, le=1, description="Weight"),
) -> dict[str, Any]:
    """The original's weighted ranking, unchanged.

    The weights are the caller's to set, because which of solubility, fold
    confidence and novelty matters is a question about the design campaign
    and not about the software. The original normalises them; a set summing
    to anything is accepted and scaled.
    """
    dispatcher = await _prepared(run_id, identity)
    payload = _call(dispatcher, "pipeline.get_hit_list", {
        "run_id": run_id,
        "limit": limit,
        "min_score": min_score,
        "rmsd_ref": rmsd_ref,
        "weights": {"soluprot": soluprot, "plddt": plddt,
                    "rmsd": rmsd, "novelty": novelty},
    })

    #  Say plainly when there is nothing to rank. Without this the console
    #  shows an empty table and the reader is left to guess whether the run
    #  had no good candidates or produced no candidates at all.
    rows = payload.get("rows")
    if not rows:
        payload["empty_reason"] = (
            "이 실행은 tier 별 후보를 남기지 않았습니다. "
            "ProteinMPNN·SoluProt·AF2 가 실제로 돌아야 순위가 생깁니다."
        )
    return payload


@router.get("/runs/{run_id}/compare", summary="Compare a run against another")
async def compare(
    run_id: str,
    identity: CurrentIdentity,
    baseline_run_id: str = Query(..., description="The run to compare against"),
) -> dict[str, Any]:
    """The original's comparison metrics. Both runs are checked and projected."""
    dispatcher = await _prepared(run_id, identity)
    #  The baseline is read too, so it needs the same check and the same
    #  projection. Checking only the subject would make the baseline a way
    #  to read a run the caller may not see.
    await _prepared(baseline_run_id, identity)

    return _call(dispatcher, "pipeline.compare_runs", {
        "run_id": run_id, "baseline_run_id": baseline_run_id,
    })


@router.get("/runs/{run_id}/quality", summary="Stage-by-stage quality signals")
async def quality(run_id: str, identity: CurrentIdentity) -> dict[str, Any]:
    """The original's per-stage reading of a run: warnings and what to do next.

    `pipeline.list_agent_events` returns what the original's panel recorded
    while the run went. A run of ours has none of those, so the panel is
    computed here from the same judgement the original applies - see
    engine/signals.py - and the two are returned together rather than mixed,
    so a reader can tell which came from where.
    """
    from foldfront.engine.signals import read_signals

    r = repos()
    run = await r.runs.get(run_id)
    if run is None:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id)

    if not await may_see_run(identity, run_id):
        raise ApiError(E.AUTH_FORBIDDEN)

    signals = read_signals(run)
    recorded: list[dict[str, Any]] = []
    try:
        dispatcher = await _prepared(run_id, identity)
        got = _call(dispatcher, "pipeline.list_agent_events", {"run_id": run_id})
        items = got.get("events") or got.get("items") or []
        if isinstance(items, list):
            recorded = [i for i in items if isinstance(i, dict)]
    except ApiError:
        #  A run with no agent_panel.jsonl is the normal case here, not a
        #  failure worth refusing the whole answer over.
        recorded = []

    return {
        "run_id": run_id,
        "signals": [s.as_dict() for s in signals],
        "counts": {
            level: sum(1 for s in signals if s.level == level)
            for level in ("info", "warning", "error")
        },
        "recorded_events": recorded,
    }


@router.post("/runs/{run_id}/project",
             dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))],
             summary="Write this run out where the original's tools can read it")
async def project_run(run_id: str, identity: CurrentIdentity) -> dict[str, Any]:
    """Project on demand.

    The read paths above do this for themselves. This exists for the case of
    running an original tool over MCP by hand, or checking what the
    projection actually wrote.
    """
    r = repos()
    if not await may_see_run(identity, run_id):
        raise ApiError(E.AUTH_FORBIDDEN)
    from foldfront.engine.legacy_view import ProjectionError

    try:
        view = await LegacyProjector(r).project(run_id)
    except ProjectionError as exc:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id) from exc

    await r.audit.record(
        "run.project", actor_id=identity.user_id, target_type="run", target_id=run_id,
        detail={"files": list(view.files)},
    )
    return view.as_dict()
