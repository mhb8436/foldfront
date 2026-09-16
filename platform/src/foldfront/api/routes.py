"""HTTP Tool API.

OpenAPI 3.1 명세가 자동으로 나온다. 현행 RAPID 의 MCP 도구 이름을 경로에 대응시켜
외부 IDE·에이전트가 같은 개념으로 접근하게 한다.

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

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from foldfront.core.auth import require
from foldfront.core.config import get_settings
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


# ---------------------------------------------------------------- 요청 본문


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


# ---------------------------------------------------------------- 실행


@router.post("/runs", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["실행"], summary="워크플로로 실행을 시작한다")
async def start_run(body: StartRunBody) -> dict[str, Any]:
    r = repos()
    wf = await r.workflows.get(body.workflow_id, body.workflow_version)
    if wf is None:
        raise HTTPException(404, f"워크플로를 찾지 못했다: {body.workflow_id}")

    try:
        run = await ExecutionService(r).start(
            wf,
            request=body.request,
            project_id=body.project_id,
            round_id=body.round_id,
            owner_id=body.owner_id,
        )
    except GraphError as exc:
        raise HTTPException(400, f"그래프 결함: {exc}") from exc

    return run.model_dump()


@router.get("/runs", tags=["실행"], summary="실행 목록")
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


@router.get("/runs/{run_id}", tags=["실행"], summary="실행 상태")
async def get_run(run_id: str) -> dict[str, Any]:
    run = await repos().runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"실행을 찾지 못했다: {run_id}")
    return run.model_dump()


@router.get("/runs/{run_id}/events", tags=["실행"], summary="실행 이벤트")
async def list_events(run_id: str, limit: int = Query(default=200, le=1000)) -> dict[str, Any]:
    items = await repos().events.list(run_id, limit=limit)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.get("/runs/{run_id}/artifacts", tags=["실행"], summary="산출물 목록")
async def list_artifacts(
    run_id: str, stage: str | None = None, user_visible: bool | None = None
) -> dict[str, Any]:
    items = await repos().artifacts.list(run_id, stage=stage, user_visible=user_visible)
    return {
        "items": [i.model_dump() for i in items],
        "count": len(items),
        "total_bytes": sum(i.size_bytes for i in items),
    }


@router.get("/runs/{run_id}/artifacts/content", tags=["실행"], summary="산출물 내용")
async def artifact_content(run_id: str, path: str) -> FileResponse:
    """산출물 실체를 내려준다 (구조 열람).

    경로를 그대로 파일시스템에 넘기지 않는다. 두 겹으로 막는다.
      1) 저장소에 **등록된** 산출물이어야 한다. 임의 경로는 애초에 조회되지 않는다.
      2) 실제 경로를 해석한 뒤 저장 루트 안에 있는지 다시 확인한다.
         등록 자체가 오염된 경우(`..` 가 섞인 경로)까지 막는다.
    """
    art = await repos().artifacts.get(run_id, path)
    if art is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 산출물이다")

    root = Path(get_settings().output_root).resolve()
    target = (root / art.path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="저장 루트를 벗어나는 경로다")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="실체 파일이 없다")

    return FileResponse(
        target,
        media_type=art.content_type or "application/octet-stream",
        filename=target.name,
    )


@router.post("/runs/{run_id}/nodes/{node_id}/complete", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["실행"],
             summary="노드 실행 결과를 보고한다 (워커가 호출한다)")
async def complete_node(run_id: str, node_id: str, body: CompleteNodeBody) -> dict[str, Any]:
    result = await service().complete_node(
        run_id, node_id, succeeded=body.succeeded, result=body.result, error=body.error
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "처리하지 못했다"))
    return result


@router.post("/runs/{run_id}/fork", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["실행"], summary="실행을 갈라 새 run 을 만든다")
async def fork_run(run_id: str, from_stage: str | None = None) -> dict[str, Any]:
    """덮어쓰지 않는 것이 기본이다."""
    child = await repos().runs.fork(run_id, from_stage=from_stage)
    if child is None:
        raise HTTPException(404, f"실행을 찾지 못했다: {run_id}")
    return child.model_dump()


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["실행"], summary="실행을 취소한다")
async def cancel_run(run_id: str, reason: str = "사용자 취소") -> dict[str, Any]:
    run = await service().cancel(run_id, reason=reason)
    if run is None:
        raise HTTPException(404, f"실행을 찾지 못했다: {run_id}")
    return run.model_dump()


# ---------------------------------------------------------------- 워크플로


@router.get("/workflows", tags=["워크플로"], summary="워크플로 목록 (식별자별 최신 버전)")
async def list_workflows(
    templates_only: bool = False, project_id: str | None = None
) -> dict[str, Any]:
    items = await repos().workflows.list(templates_only=templates_only, project_id=project_id)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.get("/workflows/{workflow_id}", tags=["워크플로"], summary="워크플로 조회")
async def get_workflow(workflow_id: str, version: int | None = None) -> dict[str, Any]:
    wf = await repos().workflows.get(workflow_id, version)
    if wf is None:
        raise HTTPException(404, f"워크플로를 찾지 못했다: {workflow_id}")
    return wf.model_dump()


@router.get("/workflows/{workflow_id}/versions", tags=["워크플로"], summary="버전 목록")
async def workflow_versions(workflow_id: str) -> dict[str, Any]:
    versions = await repos().workflows.versions(workflow_id)
    if not versions:
        raise HTTPException(404, f"워크플로를 찾지 못했다: {workflow_id}")
    return {"workflow_id": workflow_id, "versions": versions}


@router.post("/workflows", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["워크플로"], summary="워크플로를 저장한다 (새 버전으로 쌓인다)")
async def save_workflow(workflow: Workflow) -> dict[str, Any]:
    """저장 시점에 그래프를 검증한다. 실행할 때 알면 늦다."""
    try:
        graph = build_graph(workflow)
    except GraphError as exc:
        raise HTTPException(400, f"그래프 결함: {exc}") from exc

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
        #  등록되지 않은 모델은 경고만 한다 — 모델보다 워크플로를 먼저 설계할 수 있다
        "unregistered_models": missing,
    }


@router.post("/workflows/{workflow_id}/preflight", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["워크플로"],
             summary="실행 전 점검 — 그래프와 모델 해석을 미리 확인한다")
async def preflight(workflow_id: str, version: int | None = None,
                    max_gpu: int | None = None) -> dict[str, Any]:
    r = repos()
    wf = await r.workflows.get(workflow_id, version)
    if wf is None:
        raise HTTPException(404, f"워크플로를 찾지 못했다: {workflow_id}")
    return await ExecutionService(r).preflight(wf, max_gpu=max_gpu)


@router.post("/workflows/builtin", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["워크플로"],
             summary="현행 고정 단계 체인을 기본 템플릿으로 등록한다")
async def seed_builtin(stages: list[str] | None = Body(default=None)) -> dict[str, Any]:
    r = repos()
    wf = builtin_pipeline_workflow(stages=stages)
    saved = await r.workflows.save(wf)
    return saved.model_dump()


# ---------------------------------------------------------------- Model Registry


@router.get("/models", tags=["모델"], summary="Model Registry 목록")
async def list_models(
    model_id: str | None = None, kind: str | None = None, active_only: bool = False
) -> dict[str, Any]:
    items = await repos().models.list(model_id=model_id, kind=kind, active_only=active_only)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/models", dependencies=[Depends(require(Role.ADMIN))], tags=["모델"], summary="모델 버전을 등록한다")
async def register_model(mv: ModelVersion, actor_id: str | None = None) -> dict[str, Any]:
    """URL 을 직접 고치지 않고 고유 ID 로 등록한다."""
    r = repos()
    saved = await r.models.register(mv)
    await r.audit.record(
        "model.register", actor_id=actor_id, target_type="model",
        target_id=f"{mv.model_id}:{mv.version}",
        detail={"kind": str(mv.kind), "active": mv.active},
    )
    return saved.model_dump()


@router.get("/models/{model_id}/resolve", tags=["모델"],
            summary="동적 라우팅 — 실행 엔드포인트를 해석한다")
async def resolve_model(
    model_id: str, version: str | None = None, max_gpu: int | None = None
) -> dict[str, Any]:
    """모델 식별자를 실행 엔드포인트로 해석한다."""
    from foldfront.engine.router import ModelRouter, RoutingError

    try:
        route = await ModelRouter(repos().models).route(model_id, version, max_gpu=max_gpu)
    except RoutingError as exc:
        raise HTTPException(404, str(exc)) from exc
    return route.as_dict()


@router.post("/models/{model_id}/{version}/active", dependencies=[Depends(require(Role.ADMIN))], tags=["모델"], summary="활성·비활성 전환")
async def set_model_active(
    model_id: str, version: str, active: bool, actor_id: str | None = None
) -> dict[str, Any]:
    r = repos()
    mv = await r.models.set_active(model_id, version, active)
    if mv is None:
        raise HTTPException(404, f"모델을 찾지 못했다: {model_id}:{version}")
    await r.audit.record(
        "model.set_active", actor_id=actor_id, target_type="model",
        target_id=f"{model_id}:{version}", detail={"active": active},
    )
    return mv.model_dump()


@router.post("/models/{model_id}/{version}/approve", dependencies=[Depends(require(Role.ADMIN))], tags=["모델"],
             summary="사용자 정의 모델 승인·반려")
async def approve_model(
    model_id: str, version: str, approved_by: str,
    decision: Literal["approved", "rejected"] = "approved",
) -> dict[str, Any]:
    """승인·검증·롤백 절차."""
    r = repos()
    mv = await r.models.approve(model_id, version, approved_by=approved_by, decision=decision)
    if mv is None:
        raise HTTPException(404, f"모델을 찾지 못했다: {model_id}:{version}")
    await r.audit.record(
        "model.approve", actor_id=approved_by, target_type="model",
        target_id=f"{model_id}:{version}", detail={"decision": decision},
    )
    return mv.model_dump()


# ---------------------------------------------------------------- 작업 큐


@router.post("/jobs/lease", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["작업"], summary="작업을 하나 꺼낸다 (워커가 호출한다)")
async def lease_job(body: LeaseBody) -> dict[str, Any] | None:
    """우선순위가 높고 오래 기다린 작업부터 내준다."""
    job = await repos().jobs.lease(
        worker_id=body.worker_id,
        lease_seconds=body.lease_seconds,
        model_id=body.model_id,
        max_gpu=body.max_gpu,
    )
    return job.model_dump() if job else None


@router.get("/jobs/stats", tags=["작업"], summary="큐 적체 현황")
async def job_stats() -> dict[str, Any]:
    """작업 큐 적체 현황."""
    r = repos()
    stats = await r.jobs.stats()
    return {"by_status": stats, "total": sum(stats.values())}


@router.post("/jobs/reclaim", dependencies=[Depends(require(Role.SERVICE, Role.ADMIN))], tags=["작업"], summary="만료된 lease 를 회수한다")
async def reclaim_jobs() -> dict[str, Any]:
    """워커가 죽어도 작업이 영원히 잠기지 않는다."""
    return {"reclaimed": await repos().jobs.reclaim_expired()}


# ---------------------------------------------------------------- 프로젝트


@router.get("/projects", tags=["프로젝트"], summary="프로젝트 목록")
async def list_projects(include_archived: bool = False) -> dict[str, Any]:
    items = await repos().projects.list(include_archived=include_archived)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/projects", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["프로젝트"], summary="프로젝트를 만든다")
async def create_project(project: Project) -> dict[str, Any]:
    if not project.project_id:
        project.project_id = new_id("proj")
    return (await repos().projects.create(project)).model_dump()


@router.get("/projects/{project_id}/rounds", tags=["프로젝트"], summary="라운드 목록")
async def list_rounds(project_id: str) -> dict[str, Any]:
    items = await repos().rounds.list(project_id)
    return {"items": [i.model_dump() for i in items], "count": len(items)}


@router.post("/rounds", dependencies=[Depends(require(Role.RESEARCHER, Role.ADMIN))], tags=["프로젝트"], summary="라운드를 만든다")
async def create_round(round_: Round) -> dict[str, Any]:
    if not round_.round_id:
        round_.round_id = new_id("round")
    return (await repos().rounds.create(round_)).model_dump()


# ---------------------------------------------------------------- 감사


@router.get("/audit", dependencies=[Depends(require(Role.ADMIN))], tags=["운영"], summary="감사 로그 조회")
async def search_audit(
    actor_id: str | None = None, action: str | None = None,
    target_type: str | None = None, target_id: str | None = None,
    limit: int = Query(default=200, le=1000),
) -> dict[str, Any]:
    """감사 기록 조회."""
    items = await repos().audit.search(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=target_id, limit=limit,
    )
    return {"items": [i.model_dump() for i in items], "count": len(items)}
