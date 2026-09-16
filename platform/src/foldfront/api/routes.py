"""HTTP Tool API.

The OpenAPI 3.1 document is generated from these signatures. Paths mirror the
names of the original MCP tools so an IDE or an agent meets the same concepts
under either surface.

    pipeline.run          → POST /api/v1/runs
    pipeline.status       → GET  /api/v1/runs/{run_id}
    pipeline.list_runs    → GET  /api/v1/runs
    pipeline.preflight    → POST /api/v1/workflows/{id}/preflight
    pipeline.cancel_run   → POST /api/v1/runs/{run_id}/cancel
    pipeline.list_artifacts → GET /api/v1/runs/{run_id}/artifacts
    pipeline.model_provider_list/update → /api/v1/models
"""

from __future__ import annotations

from typing import Any, Literal

from pathlib import Path

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from foldfront.core.auth import CurrentIdentity, auth_mode, require
from foldfront.core.config import get_settings
from foldfront.core.errors import ApiError, E
from foldfront.db.models import (
    ModelVersion,
    Project,
    Role,
    Round,
    RunStatus,
    Workflow,
)
from foldfront.db.repositories import Repos, new_id
from foldfront.engine.dag import (
    GraphError,
    build_graph,
    builtin_pipeline_workflow,
    validate_model_refs,
)
from foldfront.engine.service import ExecutionService

router = APIRouter(prefix="/api/v1")


def repos() -> Repos:
    return Repos()


def service() -> ExecutionService:
    return ExecutionService(Repos())


# ---------------------------------------------------------------- request bodies


class StartRunBody(BaseModel):
    workflow_id: str = Field(description="실행할 워크플로 식별자")
    workflow_version: int | None = Field(default=None, description="비우면 최신 버전")
    request: dict[str, Any] = Field(default_factory=dict, description="현행 PipelineRequest 호환 입력")
    project_id: str | None = None
    round_id: str | None = None
    owner_id: str | None = None


class CompleteNodeBody(BaseModel):
    succeeded: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LeaseBody(BaseModel):
    worker_id: str
    lease_seconds: int = 900
    max_gpu: int | None = None
    model_id: str | None = None


# ---------------------------------------------------------------- runs


@router.post("/runs", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Runs"], summary="Start a run from a workflow")
async def start_run(body: StartRunBody, identity: CurrentIdentity) -> dict[str, Any]:
    r = repos()
    wf = await r.workflows.get(body.workflow_id, body.workflow_version)
    if wf is None:
        raise ApiError(E.WORKFLOW_NOT_FOUND, workflow_id=body.workflow_id)

    try:
        run = await ExecutionService(r).start(
            wf,
            request=body.request,
            project_id=body.project_id,
            round_id=body.round_id,
            owner_id=identity.user_id or body.owner_id,
        )
    except GraphError as exc:
        raise ApiError(E.WORKFLOW_GRAPH_INVALID, reason=str(exc)) from exc

    return run.model_dump()


@router.get("/runs", tags=["Runs"], summary="List runs")
async def list_runs(
    status: RunStatus | None = None,
    project_id: str | None = None,
    round_id: str | None = None,
    limit: int = Query(default=50, le=200),
    skip: int = 0,
) -> dict[str, Any]:
    r = repos()
    items = await r.runs.list(
        status=status, project_id=project_id, round_id=round_id, limit=limit, skip=skip
    )
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.get("/runs/{run_id}", tags=["Runs"], summary="Run status")
async def get_run(run_id: str) -> dict[str, Any]:
    run = await repos().runs.get(run_id)
    if run is None:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id)
    return run.model_dump()


@router.get("/runs/{run_id}/events", tags=["Runs"], summary="Run events")
async def list_events(run_id: str, limit: int = Query(default=200, le=1000)) -> dict[str, Any]:
    items = await repos().events.list(run_id, limit=limit)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.get("/runs/{run_id}/artifacts", tags=["Runs"], summary="List artifacts")
async def list_artifacts(
    run_id: str, stage: str | None = None, user_visible: bool | None = None
) -> dict[str, Any]:
    items = await repos().artifacts.list(run_id, stage=stage, user_visible=user_visible)
    return {
        "items": [i.model_dump() for i in items],
        "count": len(items),
        "total_bytes": sum(i.size_bytes for i in items),
    }


@router.get("/runs/{run_id}/artifacts/content", tags=["Runs"], summary="Fetch an artifact")
async def artifact_content(run_id: str, path: str) -> FileResponse:
    """Serve the artifact itself, which is what the structure viewer reads.

    The path is never handed to the filesystem as given. Two checks stand in
    the way. It must belong to an artifact registered for this run, so an
    arbitrary path is not looked up at all. Then the resolved path is compared
    against the storage root, which also catches a registration that was itself
    poisoned with a traversal.
    """
    art = await repos().artifacts.get(run_id, path)
    if art is None:
        raise ApiError(E.ARTIFACT_NOT_REGISTERED)

    root = Path(get_settings().output_root).resolve()
    target = (root / art.path).resolve()
    if not target.is_relative_to(root):
        raise ApiError(E.ARTIFACT_OUTSIDE_ROOT)
    if not target.is_file():
        raise ApiError(E.ARTIFACT_FILE_MISSING)

    return FileResponse(
        target,
        media_type=art.content_type or "application/octet-stream",
        filename=target.name,
    )


@router.post("/runs/{run_id}/nodes/{node_id}/complete", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["Runs"],
             summary="Report a node result (called by a worker)")
async def complete_node(run_id: str, node_id: str, body: CompleteNodeBody) -> dict[str, Any]:
    result = await service().complete_node(
        run_id, node_id, succeeded=body.succeeded, result=body.result, error=body.error
    )
    if not result.get("ok"):
        raise ApiError(E.RUN_NODE_FAILED, reason=result.get("error", ""))
    return result


@router.post("/runs/{run_id}/fork", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Runs"], summary="Fork a run")
async def fork_run(
    run_id: str, identity: CurrentIdentity, from_stage: str | None = None,
) -> dict[str, Any]:
    """Forking never writes to the run it came from."""
    r = repos()
    child = await r.runs.fork(run_id, from_stage=from_stage)
    if child is None:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id)
    await r.audit.record(
        "run.fork", actor_id=identity.user_id, target_type="run", target_id=child.run_id,
        detail={"from_run": run_id, "from_stage": from_stage},
    )
    return child.model_dump()


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Runs"], summary="Cancel a run")
async def cancel_run(
    run_id: str, identity: CurrentIdentity, reason: str = "사용자 취소",
) -> dict[str, Any]:
    run = await service().cancel(run_id, reason=reason)
    if run is None:
        raise ApiError(E.RUN_NOT_FOUND, run_id=run_id)
    await repos().audit.record(
        "run.cancel", actor_id=identity.user_id, target_type="run", target_id=run_id,
        detail={"reason": reason},
    )
    return run.model_dump()


# ---------------------------------------------------------------- workflows


@router.get("/workflows", tags=["Workflows"], summary="List workflows (latest version of each)")
async def list_workflows(
    templates_only: bool = False, project_id: str | None = None
) -> dict[str, Any]:
    items = await repos().workflows.list(templates_only=templates_only, project_id=project_id)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.get("/workflows/{workflow_id}", tags=["Workflows"], summary="Fetch a workflow")
async def get_workflow(workflow_id: str, version: int | None = None) -> dict[str, Any]:
    wf = await repos().workflows.get(workflow_id, version)
    if wf is None:
        raise ApiError(E.WORKFLOW_NOT_FOUND, workflow_id=workflow_id)
    return wf.model_dump()


@router.get("/workflows/{workflow_id}/versions", tags=["Workflows"], summary="List versions")
async def workflow_versions(workflow_id: str) -> dict[str, Any]:
    versions = await repos().workflows.versions(workflow_id)
    if not versions:
        raise ApiError(E.WORKFLOW_NOT_FOUND, workflow_id=workflow_id)
    return {"workflow_id": workflow_id, "versions": versions}


@router.post("/workflows", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Workflows"], summary="Save a workflow as a new version")
async def save_workflow(workflow: Workflow) -> dict[str, Any]:
    """Validate on save. Finding a defect at run time costs GPU hours."""
    try:
        graph = build_graph(workflow)
    except GraphError as exc:
        raise ApiError(E.WORKFLOW_GRAPH_INVALID, reason=str(exc)) from exc

    r = repos()
    known = {m.model_id for m in await r.models.list(active_only=False)}
    missing = validate_model_refs(workflow, known)

    saved = await r.workflows.save(workflow)
    await r.audit.record(
        "workflow.save", actor_id=workflow.owner_id, target_type="workflow",
        target_id=f"{saved.workflow_id}:{saved.version}",
    )
    return {
        "workflow": saved.model_dump(),
        "levels": [list(layer) for layer in graph.levels],
        #  Unregistered models warn rather than refuse: designing a workflow
        #  before registering its models is a reasonable order to work in
        "unregistered_models": missing,
    }


@router.post("/workflows/{workflow_id}/preflight", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Workflows"],
             summary="Preflight: validate the graph and resolve its models")
async def preflight(workflow_id: str, version: int | None = None,
                    max_gpu: int | None = None) -> dict[str, Any]:
    r = repos()
    wf = await r.workflows.get(workflow_id, version)
    if wf is None:
        raise ApiError(E.WORKFLOW_NOT_FOUND, workflow_id=workflow_id)
    return await ExecutionService(r).preflight(wf, max_gpu=max_gpu)


@router.post("/workflows/builtin", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Workflows"],
             summary="Register the original fixed chain as a template")
async def seed_builtin(
    identity: CurrentIdentity, stages: list[str] | None = Body(default=None),
) -> dict[str, Any]:
    r = repos()
    wf = builtin_pipeline_workflow(stages=stages)
    saved = await r.workflows.save(wf)
    await r.audit.record(
        "workflow.seed", actor_id=identity.user_id, target_type="workflow",
        target_id=f"{saved.workflow_id}:{saved.version}",
    )
    return saved.model_dump()


# ---------------------------------------------------------------- Model Registry


@router.get("/models", tags=["Models"], summary="List registered models")
async def list_models(
    model_id: str | None = None, kind: str | None = None, active_only: bool = False
) -> dict[str, Any]:
    items = await repos().models.list(model_id=model_id, kind=kind, active_only=active_only)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/models", dependencies=[Depends(require(Role.ADMIN))], tags=["Models"], summary="Register a model version")
async def register_model(mv: ModelVersion, identity: CurrentIdentity) -> dict[str, Any]:
    """Register by id, so no URL has to be edited to add a model."""
    r = repos()
    saved = await r.models.register(mv)
    await r.audit.record(
        "model.register", actor_id=identity.user_id, target_type="model",
        target_id=f"{mv.model_id}:{mv.version}",
        detail={"kind": str(mv.kind), "active": mv.active},
    )
    return saved.model_dump()


@router.get("/models/{model_id}/resolve", tags=["Models"],
            summary="Resolve a model to an execution endpoint")
async def resolve_model(
    model_id: str, version: str | None = None, max_gpu: int | None = None
) -> dict[str, Any]:
    """Resolve a model id to the endpoint that will run it."""
    from foldfront.engine.router import ModelRouter, RoutingError

    try:
        route = await ModelRouter(repos().models).route(model_id, version, max_gpu=max_gpu)
    except RoutingError as exc:
        raise ApiError(E.MODEL_UNRESOLVABLE, reason=str(exc)) from exc
    return route.as_dict()


@router.post("/models/{model_id}/{version}/active", dependencies=[Depends(require(Role.ADMIN))], tags=["Models"], summary="Activate or deactivate a version")
async def set_model_active(
    model_id: str, version: str, active: bool, identity: CurrentIdentity
) -> dict[str, Any]:
    r = repos()
    mv = await r.models.set_active(model_id, version, active)
    if mv is None:
        raise ApiError(E.MODEL_NOT_FOUND, model_id=f"{model_id}:{version}")
    await r.audit.record(
        "model.set_active", actor_id=identity.user_id, target_type="model",
        target_id=f"{model_id}:{version}", detail={"active": active},
    )
    return mv.model_dump()


@router.post("/models/{model_id}/{version}/approve", dependencies=[Depends(require(Role.ADMIN))], tags=["Models"],
             summary="Approve or reject a registered model")
async def approve_model(
    model_id: str, version: str, identity: CurrentIdentity,
    decision: Literal["approved", "rejected"] = "approved",
) -> dict[str, Any]:
    """Approve, reject or roll back a registered version."""
    r = repos()
    mv = await r.models.approve(
        model_id, version, approved_by=identity.user_id, decision=decision
    )
    if mv is None:
        raise ApiError(E.MODEL_NOT_FOUND, model_id=f"{model_id}:{version}")
    await r.audit.record(
        "model.approve", actor_id=identity.user_id, target_type="model",
        target_id=f"{model_id}:{version}", detail={"decision": decision},
    )
    return mv.model_dump()


# ---------------------------------------------------------------- job queue


@router.post("/jobs/lease", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["Jobs"], summary="Lease one job (called by a worker)")
async def lease_job(body: LeaseBody) -> dict[str, Any] | None:
    """Hand out work: highest priority, longest waiting."""
    job = await repos().jobs.lease(
        worker_id=body.worker_id,
        lease_seconds=body.lease_seconds,
        model_id=body.model_id,
        max_gpu=body.max_gpu,
    )
    return job.model_dump() if job else None


@router.get("/jobs/stats", tags=["Jobs"], summary="Queue depth")
async def job_stats() -> dict[str, Any]:
    """How much work is queued, and in what state."""
    r = repos()
    stats = await r.jobs.stats()
    return {"by_status": stats, "total": sum(stats.values())}


@router.post("/jobs/reclaim", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["Jobs"], summary="Reclaim expired leases")
async def reclaim_jobs(identity: CurrentIdentity) -> dict[str, Any]:
    """Return jobs whose worker died, so nothing stays locked."""
    r = repos()
    n = await r.jobs.reclaim_expired()
    #  Worth a record even at zero: it says an operator looked, and when.
    await r.audit.record(
        "jobs.reclaim", actor_id=identity.user_id, target_type="job", detail={"reclaimed": n},
    )
    return {"reclaimed": n}


# ---------------------------------------------------------------- projects


@router.get("/projects", tags=["Projects"], summary="List projects")
async def list_projects(include_archived: bool = False) -> dict[str, Any]:
    items = await repos().projects.list(include_archived=include_archived)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/projects", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Projects"], summary="Create a project")
async def create_project(project: Project, identity: CurrentIdentity) -> dict[str, Any]:
    if not project.project_id:
        project.project_id = new_id("proj")
    r = repos()
    saved = await r.projects.create(project)
    await r.audit.record(
        "project.create", actor_id=identity.user_id, target_type="project",
        target_id=saved.project_id,
    )
    return saved.model_dump()


@router.get("/projects/{project_id}/rounds", tags=["Projects"], summary="List rounds")
async def list_rounds(project_id: str) -> dict[str, Any]:
    items = await repos().rounds.list(project_id)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/rounds", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["Projects"], summary="Create a round")
async def create_round(round_: Round, identity: CurrentIdentity) -> dict[str, Any]:
    if not round_.round_id:
        round_.round_id = new_id("round")
    r = repos()
    saved = await r.rounds.create(round_)
    await r.audit.record(
        "round.create", actor_id=identity.user_id, target_type="round",
        target_id=saved.round_id, detail={"project_id": saved.project_id},
    )
    return saved.model_dump()


# ---------------------------------------------------------------- identity


@router.get("/me", tags=["Operations"], summary="Who the caller is, and what they may do")
async def me(identity: CurrentIdentity) -> dict[str, Any]:
    """The console needs this to decide what to offer.

    Permission is enforced on every write path regardless; hiding a control the
    caller cannot use is a courtesy, not the check. Sending the roles rather
    than a list of permitted actions keeps that boundary clear - the server
    says who you are, the console decides what to draw.
    """
    return {
        "user_id": identity.user_id,
        "email": identity.email,
        "roles": [str(r) for r in identity.roles],
        "authenticated": identity.authenticated,
        "auth_mode": auth_mode(),
    }


# ---------------------------------------------------------------- dashboard


@router.get("/summary", tags=["Operations"], summary="Everything the dashboard shows")
async def summary(recent: int = Query(default=5, le=20)) -> dict[str, Any]:
    """Everything the dashboard shows, in one call.

    The same numbers are reachable through the individual endpoints, but a
    landing screen asking for six of them would be six round trips before it
    could draw anything.
    """
    r = repos()

    runs = await r.runs.list(limit=200)
    by_status: dict[str, int] = {}
    for run in runs:
        by_status[str(run.status)] = by_status.get(str(run.status), 0) + 1

    jobs = await r.jobs.stats()
    models = await r.models.list()
    workflows = await r.workflows.list()

    #  What is occupied right now, rather than what was registered as needed
    gpu_in_use = sum(j.resources.gpu_count for j in await r.jobs.leased())

    return {
        "runs": {
            "total": len(runs),
            "by_status": by_status,
            "recent": [
                {
                    "run_id": x.run_id,
                    "status": str(x.status),
                    "workflow_id": x.workflow_id,
                    "stages_done": sum(1 for s in x.stages if str(s.status) == "succeeded"),
                    "stages_total": len(x.stages),
                    "started_at": x.started_at,
                    "finished_at": x.finished_at,
                }
                for x in runs[:recent]
            ],
        },
        "jobs": {"by_status": jobs, "total": sum(jobs.values()), "gpu_in_use": gpu_in_use},
        "models": {
            "total": len(models),
            "active": sum(1 for m in models if m.active),
            "pending_approval": [
                {"model_id": m.model_id, "version": m.version, "kind": str(m.kind)}
                for m in models
                if str(m.approval_status) != "approved"
            ],
        },
        "workflows": {
            "total": len(workflows),
            "items": [
                {"workflow_id": w.workflow_id, "name": w.name, "version": w.version,
                 "nodes": len(w.nodes), "is_builtin": w.is_builtin}
                for w in workflows
            ],
        },
        "audit": [
            {"action": a.action, "actor_id": a.actor_id,
             "target_id": a.target_id, "result": a.result, "created_at": a.created_at}
            for a in await r.audit.search(limit=recent)
        ],
    }


# ---------------------------------------------------------------- audit


@router.get("/audit", dependencies=[Depends(require(Role.ADMIN))], tags=["Operations"], summary="Search the audit trail")
async def search_audit(
    actor_id: str | None = None, action: str | None = None,
    target_type: str | None = None, target_id: str | None = None,
    limit: int = Query(default=200, le=1000),
) -> dict[str, Any]:
    """Search the audit trail."""
    items = await repos().audit.search(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=target_id, limit=limit,
    )
    return {"items": [i.model_dump() for i in items], "count": len(items)}
