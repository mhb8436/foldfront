"""워커·어댑터 시험 — 큐에서 꺼내 실행하고 다음 노드로 이어지는지 본다."""

from __future__ import annotations

import asyncio
import socket

import pytest

from foldfront.db.client import C, ensure_indexes
from foldfront.db.models import JobStatus, ModelVersion, ResourceSpec, RunStatus
from foldfront.db.repositories import Repos
from foldfront.engine.adapters import (
    AdapterError,
    AdapterRegistry,
    MockAdapter,
)
from foldfront.engine.dag import BUILTIN_STAGE_CHAIN, builtin_pipeline_workflow
from foldfront.engine.router import Route
from foldfront.engine.service import ExecutionService
from foldfront.engine.worker import Worker


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def repos():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest_worker"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


@pytest.fixture
async def seeded(repos: Repos):
    for name in BUILTIN_STAGE_CHAIN:
        await repos.models.register(ModelVersion(
            model_id=name, version="v1", endpoint_id=f"ep-{name}",
            active=True, is_default=True,
            resources=ResourceSpec(gpu_count=0 if name == "msa" else 1),
        ))
    return repos


def mock_worker(repos: Repos, **kw) -> Worker:
    return Worker(repos=repos, adapters=AdapterRegistry(mock=True), **kw)


# ---------------------------------------------------------------- 어댑터


async def test_모의_어댑터는_단계별_지표를_낸다():
    """조건 분기가 참조할 값을 내야 한다."""
    reg = AdapterRegistry(mock=True)
    route = Route(
        model_id="soluprot", version="v1", transport="runpod", target="ep",
        resources=ResourceSpec(), timeout_seconds=60.0,
    )

    result = await reg.invoke(route, {})

    assert "pass_rate" in result
    assert result["_mock"] is True
    assert result["_model"] == "soluprot:v1"


async def test_모의_어댑터는_지정한_모델만_실패시킨다():
    reg = AdapterRegistry(mock=True, mock_adapter=MockAdapter(fail_models=frozenset({"af2"})))
    ok = Route("msa", "v1", "runpod", "ep", ResourceSpec(), 60.0)
    bad = Route("af2", "v1", "runpod", "ep", ResourceSpec(), 60.0)

    assert await reg.invoke(ok, {})
    with pytest.raises(AdapterError, match="모의 실패"):
        await reg.invoke(bad, {})


async def test_컨테이너_전송은_아직_직접_실행하지_않는다():
    reg = AdapterRegistry()
    route = Route("x", "v1", "container", "img:1", ResourceSpec(), 60.0)

    with pytest.raises(AdapterError, match="컨테이너 실행은"):
        reg.pick(route)


async def test_RunPod_키가_없으면_거부한다():
    from foldfront.engine.adapters import RunPodAdapter

    route = Route("x", "v1", "runpod", "ep", ResourceSpec(), 60.0)

    with pytest.raises(AdapterError, match="RUNPOD_API_KEY"):
        await RunPodAdapter(api_key="").invoke(route, {})


# ---------------------------------------------------------------- 워커


async def test_워커가_작업을_꺼내_처리하고_다음_노드를_띄운다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa", "design", "af2"]))
    run = await svc.start(wf)

    worked = await mock_worker(seeded).step()

    assert worked is True
    jobs = {j.node_id: j.status for j in await seeded.jobs.list_for_run(run.run_id)}
    assert jobs["msa"] is JobStatus.SUCCEEDED
    #  다음 노드가 큐에 들어와 있다
    assert jobs["design"] is JobStatus.QUEUED


async def test_큐가_비면_거짓을_낸다(repos: Repos):
    assert await mock_worker(repos).step() is False
    assert mock_worker(repos).stats.idle_polls == 0  # 새 워커라 0


async def test_끝까지_돌리면_run_이_성공한다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow())
    run = await svc.start(wf)

    stats = await mock_worker(seeded).drain()

    assert stats.succeeded == len(BUILTIN_STAGE_CHAIN)
    final = await seeded.runs.get(run.run_id)
    assert final.status is RunStatus.SUCCEEDED
    #  단계마다 모의 지표가 남는다
    msa = next(s for s in final.stages if s.name == "msa")
    assert "depth" in msa.metrics


async def test_모델이_실패하면_run_이_실패로_닫힌다(seeded: Repos):
    svc = ExecutionService(seeded)
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa", "design", "af2"]))
    run = await svc.start(wf)

    worker = Worker(
        repos=seeded,
        adapters=AdapterRegistry(mock=True, mock_adapter=MockAdapter(fail_models=frozenset({"design"}))),
    )
    await worker.drain()

    final = await seeded.runs.get(run.run_id)
    assert final.status is RunStatus.FAILED
    stages = {s.name: s for s in final.stages}
    assert stages["design"].status is RunStatus.FAILED
    assert "모의 실패" in stages["design"].error
    assert stages["af2"].status is RunStatus.CANCELLED
    assert worker.stats.failed == 1


async def test_병렬_노드를_워커_하나가_차례로_처리한다(seeded: Repos):
    from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode

    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-par", name="병렬",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="rfd3", kind=NodeKind.MODEL, model_id="rfd3"),
            WorkflowNode(node_id="bioemu", kind=NodeKind.MODEL, model_id="bioemu"),
            WorkflowNode(node_id="join", kind=NodeKind.JOIN),
            WorkflowNode(node_id="design", kind=NodeKind.MODEL, model_id="design"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="rfd3"),
            WorkflowEdge(source="msa", target="bioemu"),
            WorkflowEdge(source="rfd3", target="join"),
            WorkflowEdge(source="bioemu", target="join"),
            WorkflowEdge(source="join", target="design"),
        ],
    ))
    run = await ExecutionService(seeded).start(wf)

    stats = await mock_worker(seeded).drain()

    #  제어 노드는 큐를 거치지 않으므로 모델 노드 4개만 처리한다
    assert stats.succeeded == 4
    assert (await seeded.runs.get(run.run_id)).status is RunStatus.SUCCEEDED


async def test_워커_둘이_같은_작업을_집지_않는다(seeded: Repos):
    """워커가 여럿이어도 같은 작업을 두 번 잡지 않는다."""
    from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode

    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-race", name="경합",
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
    run = await ExecutionService(seeded).start(wf)
    await mock_worker(seeded).step()   # msa 를 끝내 병렬 둘을 큐에 올린다

    w1, w2 = mock_worker(seeded), mock_worker(seeded)
    await asyncio.gather(w1.step(), w2.step())

    #  둘이 하나씩 나눠 가진다 — 같은 작업을 두 번 처리하지 않는다
    assert w1.stats.leased == 1
    assert w2.stats.leased == 1
    jobs = await seeded.jobs.list_for_run(run.run_id)
    assert len([j for j in jobs if j.status is JobStatus.SUCCEEDED]) == 3


async def test_자원이_부족한_워커는_큰_작업을_건너뛴다(seeded: Repos):
    """모델별 자원 요구량을 반영해 스케줄링한다."""
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa", "rfd3"]))
    await ExecutionService(seeded).start(wf)

    #  GPU 를 못 쓰는 워커는 msa(gpu 0)만 처리하고 rfd3(gpu 1)은 두고 간다
    cpu_only = mock_worker(seeded, max_gpu=0)
    await cpu_only.drain()

    assert cpu_only.stats.succeeded == 1
    stats = await seeded.jobs.stats()
    assert stats.get(JobStatus.QUEUED) == 1


async def test_라우팅_정보가_없는_작업은_실패로_닫는다(seeded: Repos):
    """작업이 잘못 만들어져도 워커가 멈추지 않아야 한다."""
    from foldfront.db.models import Job

    await seeded.jobs.enqueue(Job(job_id="j-bad", run_id="run-x", node_id="n1"))

    worker = mock_worker(seeded)
    assert await worker.step() is True
    assert worker.stats.failed == 1

    job = await seeded.jobs.col.find_one({"job_id": "j-bad"})
    assert job["status"] == JobStatus.FAILED
    assert "라우팅 정보가 없" in job["error"]


async def test_만료된_작업을_다른_워커가_이어받는다(seeded: Repos):
    """워커가 죽어도 작업이 영원히 잠기지 않는다."""
    wf = await seeded.workflows.save(builtin_pipeline_workflow(stages=["msa"]))
    run = await ExecutionService(seeded).start(wf)

    #  워커 하나가 집었다가 죽은 상황
    await seeded.jobs.lease(worker_id="dead-worker", lease_seconds=-1)
    assert await mock_worker(seeded).step() is False   # 큐에 잡을 것이 없다

    reclaimed = await seeded.jobs.reclaim_expired()
    assert reclaimed == 1

    #  회수된 뒤에는 다른 워커가 처리한다
    worker = mock_worker(seeded)
    assert await worker.step() is True
    assert (await seeded.runs.get(run.run_id)).status is RunStatus.SUCCEEDED


async def test_조건분기_실행이_워커를_통해_돈다(seeded: Repos):
    """모의 지표로 분기가 갈린다."""
    from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode

    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-gate", name="분기",
        nodes=[
            WorkflowNode(node_id="soluprot", kind=NodeKind.MODEL, model_id="soluprot"),
            WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="soluprot.pass_rate >= 0"),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2"),
            WorkflowNode(node_id="novelty", kind=NodeKind.MODEL, model_id="novelty"),
        ],
        edges=[
            WorkflowEdge(source="soluprot", target="gate"),
            WorkflowEdge(source="gate", target="af2", branch="true"),
            WorkflowEdge(source="gate", target="novelty", branch="false"),
        ],
    ))
    run = await ExecutionService(seeded).start(wf)

    await mock_worker(seeded).drain()

    final = await seeded.runs.get(run.run_id)
    stages = {s.name: s.status for s in final.stages}
    #  pass_rate 는 항상 0 이상이므로 참 가지가 돈다
    assert stages["af2"] is RunStatus.SUCCEEDED
    assert stages["novelty"] is RunStatus.CANCELLED
    assert final.status is RunStatus.SUCCEEDED


async def test_run_forever_는_중지_신호로_멈춘다(seeded: Repos):
    worker = mock_worker(seeded, poll_interval=0.01)
    stop = asyncio.Event()

    task = asyncio.create_task(worker.run_forever(stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert worker.stats.idle_polls > 0


async def test_조건분기_노드는_큐에_들어가지_않는다(seeded: Repos):
    """회귀 시험 — BRANCH 가 큐에 들어가 「라우팅 정보가 없」로 실패하던 결함.

    BRANCH 는 조건만 평가하고 FANOUT·JOIN 은 흐름만 가른다. 셋 다 모델이 없으므로
    큐에 넣으면 워커가 호출할 대상이 없다.
    """
    from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode

    wf = await seeded.workflows.save(Workflow(
        workflow_id="wf-nq", name="분기",
        nodes=[
            WorkflowNode(node_id="soluprot", kind=NodeKind.MODEL, model_id="soluprot"),
            WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="soluprot.pass_rate >= 0"),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2"),
            WorkflowNode(node_id="novelty", kind=NodeKind.MODEL, model_id="novelty"),
        ],
        edges=[
            WorkflowEdge(source="soluprot", target="gate"),
            WorkflowEdge(source="gate", target="af2", branch="true"),
            WorkflowEdge(source="gate", target="novelty", branch="false"),
        ],
    ))
    run = await ExecutionService(seeded).start(wf)

    worker = mock_worker(seeded)
    await worker.drain()

    #  큐에 들어간 것은 모델 노드뿐이다
    node_ids = {j.node_id for j in await seeded.jobs.list_for_run(run.run_id)}
    assert "gate" not in node_ids
    assert worker.stats.failed == 0
