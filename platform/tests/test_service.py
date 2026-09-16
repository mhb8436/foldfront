"""실행 서비스 시험 — 엔진·큐·저장소가 이어져 도는지 본다."""

from __future__ import annotations

import socket

import pytest

from foldfront.db.client import C, ensure_indexes
from foldfront.db.models import (
    JobStatus,
    ModelVersion,
    NodeKind,
    ResourceSpec,
    RunStatus,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from foldfront.db.repositories import Repos
from foldfront.engine.dag import builtin_pipeline_workflow
from foldfront.engine.router import ModelRouter, RoutingError
from foldfront.engine.service import ExecutionService


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def repos():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest_service"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


@pytest.fixture
async def seeded(repos: Repos):
    """현행 단계 모델을 Registry 에 넣어 둔다."""
    for name in ("msa", "rfd3", "bioemu", "design", "soluprot", "af2", "novelty"):
        await repos.models.register(ModelVersion(
            model_id=name, version="v1", endpoint_id=f"ep-{name}",
            active=True, is_default=True,
            resources=ResourceSpec(gpu_count=1 if name != "msa" else 0),
        ))
    return repos


# ---------------------------------------------------------------- 라우팅


async def test_라우팅은_엔드포인트를_고른다(seeded: Repos):
    """라우팅이 모델을 해석한다."""
    route = await ModelRouter(seeded.models).route("rfd3")

    assert route.transport == "runpod"
    assert route.target == "ep-rfd3"
    assert route.version == "v1"


async def test_엔드포인트가_없으면_URL_그다음_이미지를_쓴다(repos: Repos):
    await repos.models.register(ModelVersion(
        model_id="soluprot", version="v1", base_url="http://127.0.0.1:9000",
        active=True, is_default=True,
    ))
    await repos.models.register(ModelVersion(
        model_id="custom", version="v1", container_image="ghcr.io/x/c:1",
        active=True, is_default=True,
    ))

    r1 = await ModelRouter(repos.models).route("soluprot")
    r2 = await ModelRouter(repos.models).route("custom")

    assert (r1.transport, r1.target) == ("http", "http://127.0.0.1:9000")
    assert (r2.transport, r2.target) == ("container", "ghcr.io/x/c:1")


async def test_실행_위치가_없으면_거부한다(repos: Repos):
    await repos.models.register(ModelVersion(
        model_id="empty", version="v1", active=True, is_default=True,
    ))

    with pytest.raises(RoutingError, match="실행 위치가 없다"):
        await ModelRouter(repos.models).route("empty")


async def test_자원_정책이_맞지_않으면_라우팅하지_않는다(repos: Repos):
    await repos.models.register(ModelVersion(
        model_id="big", version="v1", endpoint_id="ep", active=True, is_default=True,
        resources=ResourceSpec(gpu_count=4),
    ))

    with pytest.raises(RoutingError, match="GPU 4개를 요구한다"):
        await ModelRouter(repos.models).route("big", max_gpu=1)


async def test_없는_모델은_사유를_알린다(repos: Repos):
    with pytest.raises(RoutingError, match="찾지 못했다"):
        await ModelRouter(repos.models).route("없는모델")


async def test_preflight_는_실패를_모아서_낸다(seeded: Repos):
    """실행 중간에 멈추는 것보다 미리 알리는 편이 낫다."""
    result = await ModelRouter(seeded.models).preflight(
        [("msa", None), ("없는모델", None), ("af2", None)]
    )

    assert result["ok"] is False
    assert len(result["resolved"]) == 2
    assert len(result["errors"]) == 1


# ---------------------------------------------------------------- 실행


async def test_정형체인을_시작하면_첫_노드만_큐에_들어간다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())

    run = await svc.start(wf, request={"target_fasta": "x.fasta"})

    assert run.status is RunStatus.RUNNING
    jobs = await seeded.jobs.list_for_run(run.run_id)
    assert [j.node_id for j in jobs] == ["msa"]
    #  라우팅 결과가 작업에 실려 간다
    assert jobs[0].payload["route"]["target"] == "ep-msa"


async def test_노드를_끝내면_다음_노드가_큐에_들어간다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())
    run = await svc.start(wf)

    res = await svc.complete_node(run.run_id, "msa", succeeded=True, result={"depth": 120})

    assert res["ok"] is True
    assert res["queued"] == ["rfd3"]
    assert res["done"] is False


async def test_끝까지_돌리면_run_이_성공으로_닫힌다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())
    run = await svc.start(wf)

    from foldfront.engine.dag import BUILTIN_STAGE_CHAIN

    for node_id in BUILTIN_STAGE_CHAIN:
        await svc.complete_node(run.run_id, node_id, succeeded=True, result={"ok": True})

    final = await seeded.runs.get(run.run_id)
    assert final.status is RunStatus.SUCCEEDED
    assert final.finished_at is not None
    assert all(s.status is RunStatus.SUCCEEDED for s in final.stages)


async def test_병렬_노드는_함께_큐에_들어간다(seeded: Repos):
    """같은 층의 노드가 함께 큐에 들어간다."""
    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-par", name="병렬",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="rfd3", kind=NodeKind.MODEL, model_id="rfd3"),
            WorkflowNode(node_id="bioemu", kind=NodeKind.MODEL, model_id="bioemu"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="rfd3"),
            WorkflowEdge(source="msa", target="bioemu"),
        ],
    ))
    svc = ExecutionService(seeded)
    run = await svc.start(wf)

    res = await svc.complete_node(run.run_id, "msa", succeeded=True)

    assert sorted(res["queued"]) == ["bioemu", "rfd3"]


async def test_제어노드는_큐를_거치지_않는다(seeded: Repos):
    """FANOUT·JOIN 은 실행할 것이 없으므로 즉시 통과시킨다."""
    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-ctrl", name="제어",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="split", kind=NodeKind.FANOUT),
            WorkflowNode(node_id="rfd3", kind=NodeKind.MODEL, model_id="rfd3"),
            WorkflowNode(node_id="bioemu", kind=NodeKind.MODEL, model_id="bioemu"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="split"),
            WorkflowEdge(source="split", target="rfd3"),
            WorkflowEdge(source="split", target="bioemu"),
        ],
    ))
    svc = ExecutionService(seeded)
    run = await svc.start(wf)

    res = await svc.complete_node(run.run_id, "msa", succeeded=True)

    #  split 은 큐에 들어가지 않고 그 자식이 바로 들어간다
    assert sorted(res["queued"]) == ["bioemu", "rfd3"]
    stages = {s.name: s.status for s in (await seeded.runs.get(run.run_id)).stages}
    assert stages["split"] is RunStatus.SUCCEEDED


async def test_실패하면_후속_노드가_취소로_기록된다(seeded: Repos):
    """화면이 건너뛴 이유를 보여줘야 한다."""
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa", "design", "af2"]))
    run = await svc.start(wf)

    await svc.complete_node(run.run_id, "msa", succeeded=True)
    res = await svc.complete_node(run.run_id, "design", succeeded=False, error="GPU 없음")

    assert res["done"] is True
    final = await seeded.runs.get(run.run_id)
    assert final.status is RunStatus.FAILED
    stages = {s.name: s for s in final.stages}
    assert stages["design"].status is RunStatus.FAILED
    assert stages["design"].error == "GPU 없음"
    assert stages["af2"].status is RunStatus.CANCELLED
    assert "선행 노드" in stages["af2"].error


async def test_조건분기가_실행에서도_동작한다(seeded: Repos):
    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-branch", name="분기",
        nodes=[
            WorkflowNode(node_id="soluprot", kind=NodeKind.MODEL, model_id="soluprot"),
            WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="soluprot.pass_rate > 0.3"),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2"),
            WorkflowNode(node_id="novelty", kind=NodeKind.MODEL, model_id="novelty"),
        ],
        edges=[
            WorkflowEdge(source="soluprot", target="gate"),
            WorkflowEdge(source="gate", target="af2", branch="true"),
            WorkflowEdge(source="gate", target="novelty", branch="false"),
        ],
    ))
    svc = ExecutionService(seeded)
    run = await svc.start(wf)

    await svc.complete_node(run.run_id, "soluprot", succeeded=True, result={"pass_rate": 0.7})
    await svc.complete_node(run.run_id, "gate", succeeded=True, result={})

    stages = {s.name: s.status for s in (await seeded.runs.get(run.run_id)).stages}
    assert stages["novelty"] is RunStatus.CANCELLED
    jobs = {j.node_id for j in await seeded.jobs.list_for_run(run.run_id)}
    assert "af2" in jobs
    assert "novelty" not in jobs


async def test_라우팅에_실패하면_노드가_실패로_닫힌다(repos: Repos):
    """모델을 등록하지 않고 실행하면 그 노드에서 멈춘다."""
    await repos.models.register(ModelVersion(
        model_id="msa", version="v1", endpoint_id="ep", active=True, is_default=True,
    ))
    wf = await repos.workflows.save(builtin_pipeline_workflow(stages=["msa", "design"]))
    svc = ExecutionService(repos)
    run = await svc.start(wf)

    await svc.complete_node(run.run_id, "msa", succeeded=True)

    final = await repos.runs.get(run.run_id)
    design = next(s for s in final.stages if s.name == "design")
    assert design.status is RunStatus.FAILED
    assert "찾지 못했다" in design.error
    assert final.status is RunStatus.FAILED


async def test_상태를_DB_에서_복원한다(seeded: Repos):
    """워커는 상태를 들고 있지 않는다."""
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa", "design", "af2"]))
    run = await svc.start(wf)
    await svc.complete_node(run.run_id, "msa", succeeded=True, result={"depth": 100})

    #  새 서비스 인스턴스로 복원한다 — 프로세스가 죽었다 살아난 상황
    graph, plan = await ExecutionService(seeded).load_plan(run.run_id)

    assert plan.states["msa"].outcome.value == "succeeded"
    assert plan.context["msa"]["depth"] == 100
    assert plan.ready() == ("design",)


async def test_preflight_가_그래프_결함을_먼저_잡는다(seeded: Repos):
    bad = Workflow(
        workflow_id="wf-bad", name="순환",
        nodes=[
            WorkflowNode(node_id="a", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="b", kind=NodeKind.MODEL, model_id="af2"),
        ],
        edges=[
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="b", target="a"),
        ],
    )

    result = await ExecutionService(seeded).preflight(bad)

    assert result["ok"] is False
    assert "순환" in result["graph_error"]


async def test_preflight_가_층_구조를_알려준다(seeded: Repos):
    result = await ExecutionService(seeded).preflight(builtin_pipeline_workflow())

    assert result["ok"] is True
    assert result["node_count"] == 7
    assert result["levels"][0] == ["msa"]


async def test_취소하면_큐에_남은_작업도_거둔다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())
    run = await svc.start(wf)

    await svc.cancel(run.run_id, reason="사용자가 중단했다")

    final = await seeded.runs.get(run.run_id)
    assert final.status is RunStatus.CANCELLED
    jobs = await seeded.jobs.list_for_run(run.run_id)
    assert all(j.status is JobStatus.CANCELLED for j in jobs)


async def test_실행을_시작하면_감사로그가_남는다(seeded: Repos):
    """감사 기록을 남긴다."""
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())

    run = await svc.start(wf, owner_id="u-1")

    logs = await seeded.audit.search(action="run.create")
    assert len(logs) == 1
    assert logs[0].target_id == run.run_id
    assert logs[0].actor_id == "u-1"


async def test_라운드에_자동으로_연결된다(seeded: Repos):
    """프로젝트·라운드에 실행을 잇는다."""
    from foldfront.db.models import Project, Round

    await seeded.projects.create(Project(project_id="p1", name="시험"))
    await seeded.rounds.create(Round(round_id="r1", project_id="p1"))
    wf = await seeded.workflows.save(builtin_pipeline_workflow())

    run = await ExecutionService(seeded).start(wf, project_id="p1", round_id="r1")

    assert (await seeded.rounds.get("r1")).linked_run_ids == [run.run_id]


async def test_제어노드_통과가_무한재귀하지_않는다(seeded: Repos):
    """회귀 시험 — _enqueue_ready 가 제어 노드를 두고 무한히 재귀하던 결함.

    통과시킬 제어 노드가 남아 있는지로 재귀를 판단했는데, 이미 통과시킨 노드도 조건을
    만족해 같은 자리를 계속 내려갔다. 루프로 바꾸고 처리한 노드를 기억하게 했다.
    """
    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-deep", name="제어 연속",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="s1", kind=NodeKind.FANOUT),
            WorkflowNode(node_id="s2", kind=NodeKind.FANOUT),
            WorkflowNode(node_id="j1", kind=NodeKind.JOIN),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="s1"),
            WorkflowEdge(source="s1", target="s2"),
            WorkflowEdge(source="s2", target="j1"),
            WorkflowEdge(source="j1", target="af2"),
        ],
    ))
    svc = ExecutionService(seeded)
    run = await svc.start(wf)

    #  제어 노드 셋을 연달아 통과해 af2 까지 도달해야 한다
    res = await svc.complete_node(run.run_id, "msa", succeeded=True)

    assert res["queued"] == ["af2"]
    stages = {s.name: s.status for s in (await seeded.runs.get(run.run_id)).stages}
    assert stages["s1"] is RunStatus.SUCCEEDED
    assert stages["s2"] is RunStatus.SUCCEEDED
    assert stages["j1"] is RunStatus.SUCCEEDED


async def test_시작하자마자_라우팅에_실패하면_run_이_닫힌다(repos: Repos):
    """RUNNING 으로 방치되면 화면에 영원히 실행 중으로 보인다."""
    wf = await repos.workflows.save(builtin_pipeline_workflow(stages=["msa"]))

    run = await ExecutionService(repos).start(wf)

    final = await repos.runs.get(run.run_id)
    assert final.status is RunStatus.FAILED
    assert final.finished_at is not None
