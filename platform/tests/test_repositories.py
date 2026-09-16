"""저장소 계층 시험.

실제 MongoDB 에 붙어 돈다. 컬렉션은 시험 전용 DB 에 만들고 매번 비운다.
"""

from __future__ import annotations

import socket
from datetime import timedelta

import pytest

from foldfront.db.client import C, ensure_indexes
from foldfront.db.models import (
    Artifact,
    Feedback,
    Job,
    JobStatus,
    ModelKind,
    ModelVersion,
    NodeKind,
    Project,
    Report,
    ResourceSpec,
    Round,
    Run,
    RunStatus,
    StageState,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    utcnow,
)
from foldfront.db.repositories import Repos, new_id


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def repos():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


# ---------------------------------------------------------------- 실행


async def test_run_을_만들고_읽는다(repos: Repos):
    run = Run(run_id="run-a", request={"target_fasta": "/data/x.fasta", "rfd3_use": True})
    await repos.runs.create(run)

    got = await repos.runs.get("run-a")
    assert got is not None
    #  request 가 통째로 남아야 재현이 된다
    assert got.request["target_fasta"] == "/data/x.fasta"
    assert got.status is RunStatus.PENDING


async def test_단계를_갱신하면_덮어쓰고_없으면_추가한다(repos: Repos):
    """체크포인트 단위로 재실행하므로 같은 단계가 여러 번 갱신된다."""
    await repos.runs.create(Run(run_id="run-b"))

    await repos.runs.upsert_stage("run-b", StageState(name="msa", status=RunStatus.RUNNING))
    await repos.runs.upsert_stage("run-b", StageState(name="design", status=RunStatus.PENDING))
    await repos.runs.upsert_stage(
        "run-b",
        StageState(name="msa", status=RunStatus.SUCCEEDED, model_id="mmseqs", model_version="v2"),
    )

    run = await repos.runs.get("run-b")
    assert len(run.stages) == 2
    msa = next(s for s in run.stages if s.name == "msa")
    assert msa.status is RunStatus.SUCCEEDED
    #  어느 모델 버전으로 돌았는지 남는다
    assert msa.model_version == "v2"


async def test_fork_는_새_run_을_만들고_이전_단계만_승계한다(repos: Repos):
    """덮어쓰지 않는 것이 기본이다."""
    await repos.runs.create(Run(run_id="run-c", request={"a": 1}))
    for name in ("msa", "rfd3", "design", "soluprot"):
        await repos.runs.upsert_stage("run-c", StageState(name=name, status=RunStatus.SUCCEEDED))

    child = await repos.runs.fork("run-c", from_stage="design")

    assert child.run_id != "run-c"
    assert child.forked_from_run_id == "run-c"
    assert child.forked_from_stage == "design"
    #  design 이전 단계(msa·rfd3)만 남고 design 부터는 다시 실행한다
    assert [s.name for s in child.stages] == ["msa", "rfd3"]
    #  원본은 그대로다
    assert len((await repos.runs.get("run-c")).stages) == 4


async def test_상태를_바꾸면_시각이_함께_기록된다(repos: Repos):
    await repos.runs.create(Run(run_id="run-d"))

    await repos.runs.set_status("run-d", RunStatus.RUNNING)
    assert (await repos.runs.get("run-d")).started_at is not None

    await repos.runs.set_status("run-d", RunStatus.FAILED, error="GPU 없음")
    run = await repos.runs.get("run-d")
    assert run.finished_at is not None
    assert run.error == "GPU 없음"


async def test_이벤트는_순번이_이어진다(repos: Repos):
    await repos.events.append("run-e", "msa 시작", stage="msa")
    await repos.events.append("run-e", "msa 끝", stage="msa")
    await repos.events.append("run-e", "design 시작", stage="design")

    evs = await repos.events.list("run-e")
    assert [e.seq for e in evs] == [1, 2, 3]
    assert evs[0].message == "msa 시작"


async def test_아티팩트는_같은_경로를_두_번_넣지_않는다(repos: Repos):
    """재실행해도 목록이 불어나면 안 된다."""
    art = Artifact(run_id="run-f", path="af2/ranked_0.pdb", kind="pdb", size_bytes=100)
    await repos.artifacts.register(art)
    await repos.artifacts.register(Artifact(**{**art.model_dump(), "size_bytes": 200}))

    items = await repos.artifacts.list("run-f")
    assert len(items) == 1
    assert items[0].size_bytes == 200


async def test_수명이_지난_아티팩트를_찾는다(repos: Repos):
    await repos.artifacts.register(Artifact(
        run_id="run-g", path="tmp/a.json", kind="json",
        retain_until=utcnow() - timedelta(days=1),
    ))
    await repos.artifacts.register(Artifact(run_id="run-g", path="keep/b.pdb", kind="pdb"))

    expired = await repos.artifacts.expired()
    assert [a.path for a in expired] == ["tmp/a.json"]


# ---------------------------------------------------------------- Model Registry


async def test_기본_버전을_등록하면_이전_기본은_해제된다(repos: Repos):
    """기본 버전은 model_id 당 하나여야 한다."""
    await repos.models.register(ModelVersion(model_id="rfd3", version="v1", is_default=True))
    await repos.models.register(ModelVersion(model_id="rfd3", version="v2", is_default=True))

    v1 = await repos.models.get("rfd3", "v1")
    v2 = await repos.models.get("rfd3", "v2")
    assert v1.is_default is False
    assert v2.is_default is True


async def test_라우팅은_버전을_생략하면_기본_활성_버전을_고른다(repos: Repos):
    """동적 라우팅의 핵심."""
    await repos.models.register(ModelVersion(model_id="af2", version="v1", active=True))
    await repos.models.register(ModelVersion(
        model_id="af2", version="v2", active=True, is_default=True, endpoint_id="ep-v2",
    ))

    picked = await repos.models.resolve("af2")
    assert picked.version == "v2"
    assert picked.endpoint_id == "ep-v2"

    #  버전을 지정하면 그것을 고른다
    assert (await repos.models.resolve("af2", "v1")).version == "v1"


async def test_비활성_버전은_라우팅되지_않는다(repos: Repos):
    await repos.models.register(ModelVersion(model_id="esm", version="v1", active=True))
    await repos.models.set_active("esm", "v1", False)

    assert await repos.models.resolve("esm") is None


async def test_승인되지_않은_모델은_라우팅되지_않는다(repos: Repos):
    """사용자 정의 모델은 승인 전에는 운영에 반영되지 않는다."""
    await repos.models.register(ModelVersion(
        model_id="custom", version="v1", active=True, approval_status="pending",
    ))
    assert await repos.models.resolve("custom") is None

    await repos.models.approve("custom", "v1", approved_by="admin-1")
    picked = await repos.models.resolve("custom")
    assert picked is not None
    assert picked.approved_by == "admin-1"


async def test_registry_는_요구_항목을_모두_담는다(repos: Repos):
    """모델ID·버전·컨테이너·엔드포인트·입출력 스키마·자원요구량·활성 상태."""
    await repos.models.register(ModelVersion(
        model_id="proteinmpnn", version="2026-09-01", kind=ModelKind.SEQUENCE,
        container_image="ghcr.io/x/mpnn:2026-09-01", endpoint_id="ep-mpnn",
        input_schema={"required": ["pdb"]}, output_schema={"type": "object"},
        resources=ResourceSpec(gpu_count=1, gpu_memory_gb=16.0),
        weights_uri="s3://w/mpnn.pt",
    ))

    mv = await repos.models.get("proteinmpnn", "2026-09-01")
    assert mv.container_image.endswith(":2026-09-01")
    assert mv.resources.gpu_memory_gb == 16.0
    assert mv.input_schema["required"] == ["pdb"]


# ---------------------------------------------------------------- 워크플로


async def test_저장할_때마다_버전이_올라간다(repos: Repos):
    """주요 결과에 버전 관리를 제공한다."""
    wf = Workflow(workflow_id="wf-1", name="안정화", nodes=[WorkflowNode(node_id="n1")])
    saved1 = await repos.workflows.save(wf)

    wf2 = Workflow(
        workflow_id="wf-1", name="안정화 개정",
        nodes=[WorkflowNode(node_id="n1"), WorkflowNode(node_id="n2")],
    )
    saved2 = await repos.workflows.save(wf2)

    assert saved1.version == 1
    assert saved2.version == 2
    #  버전을 비우면 최신을 낸다
    assert (await repos.workflows.get("wf-1")).name == "안정화 개정"
    #  이전 버전도 남아 있다
    assert (await repos.workflows.get("wf-1", 1)).name == "안정화"
    assert await repos.workflows.versions("wf-1") == [1, 2]


async def test_목록은_workflow_별_최신_버전만_낸다(repos: Repos):
    await repos.workflows.save(Workflow(workflow_id="wf-a", name="A", is_template=True))
    await repos.workflows.save(Workflow(workflow_id="wf-a", name="A2", is_template=True))
    await repos.workflows.save(Workflow(workflow_id="wf-b", name="B", is_template=True))

    items = await repos.workflows.list(templates_only=True)
    assert sorted(w.workflow_id for w in items) == ["wf-a", "wf-b"]
    assert next(w for w in items if w.workflow_id == "wf-a").name == "A2"


async def test_DAG_는_병렬과_조건분기를_담는다(repos: Repos):
    """워크플로 저장과 판번호."""
    wf = await repos.workflows.save(Workflow(
        workflow_id="wf-dag", name="분기",
        nodes=[
            WorkflowNode(node_id="a", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="b", kind=NodeKind.FANOUT),
            WorkflowNode(node_id="c", kind=NodeKind.MODEL, model_id="rfd3"),
            WorkflowNode(node_id="d", kind=NodeKind.MODEL, model_id="bioemu"),
            WorkflowNode(node_id="e", kind=NodeKind.BRANCH, condition="pass_rate > 0.3"),
        ],
        edges=[
            WorkflowEdge(source="a", target="b"),
            WorkflowEdge(source="b", target="c"),
            WorkflowEdge(source="b", target="d"),
            WorkflowEdge(source="c", target="e"),
            WorkflowEdge(source="e", target="d", branch="true"),
        ],
    ))

    got = await repos.workflows.get("wf-dag")
    assert len(got.nodes) == 5
    assert len([e for e in got.edges if e.source == "b"]) == 2
    assert next(e for e in got.edges if e.branch == "true").target == "d"


# ---------------------------------------------------------------- 작업 큐


async def test_큐는_우선순위와_대기순으로_꺼낸다(repos: Repos):
    """작업 큐 임대."""
    await repos.jobs.enqueue(Job(job_id="j1", run_id="r", priority=1))
    await repos.jobs.enqueue(Job(job_id="j2", run_id="r", priority=9))
    await repos.jobs.enqueue(Job(job_id="j3", run_id="r", priority=9))

    first = await repos.jobs.lease(worker_id="w1")
    second = await repos.jobs.lease(worker_id="w1")
    third = await repos.jobs.lease(worker_id="w1")

    #  우선순위 9 가 먼저, 같은 우선순위면 먼저 들어온 것부터
    assert [first.job_id, second.job_id, third.job_id] == ["j2", "j3", "j1"]
    assert first.status is JobStatus.LEASED
    assert first.attempts == 1


async def test_같은_작업을_두_워커가_집지_않는다(repos: Repos):
    """find_one_and_update 가 원자적이라 경합이 생기지 않는다."""
    await repos.jobs.enqueue(Job(job_id="only", run_id="r"))

    a = await repos.jobs.lease(worker_id="w1")
    b = await repos.jobs.lease(worker_id="w2")

    assert a.job_id == "only"
    assert b is None


async def test_자원이_부족한_워커는_큰_작업을_집지_않는다(repos: Repos):
    await repos.jobs.enqueue(Job(
        job_id="big", run_id="r", resources=ResourceSpec(gpu_count=4),
    ))
    await repos.jobs.enqueue(Job(
        job_id="small", run_id="r", resources=ResourceSpec(gpu_count=1),
    ))

    got = await repos.jobs.lease(worker_id="w-1gpu", max_gpu=1)
    assert got.job_id == "small"


async def test_만료된_lease_는_큐로_되돌아온다(repos: Repos):
    job = Job(job_id="j-exp", run_id="r")
    await repos.jobs.enqueue(job)
    await repos.jobs.lease(worker_id="w1", lease_seconds=-1)  # 이미 만료된 상태로 만든다

    reclaimed = await repos.jobs.reclaim_expired()

    assert reclaimed == 1
    assert (await repos.jobs.col.find_one({"job_id": "j-exp"}))["status"] == JobStatus.QUEUED


async def test_시도_횟수를_다_쓰면_실패로_확정한다(repos: Repos):
    await repos.jobs.enqueue(Job(job_id="j-dead", run_id="r", max_attempts=1))
    await repos.jobs.lease(worker_id="w1", lease_seconds=-1)

    await repos.jobs.reclaim_expired()

    doc = await repos.jobs.col.find_one({"job_id": "j-dead"})
    assert doc["status"] == JobStatus.FAILED
    assert doc["error"] == "lease 만료"


async def test_큐_통계를_낸다(repos: Repos):
    """적체를 화면에서 본다."""
    await repos.jobs.enqueue(Job(job_id="a", run_id="r"))
    await repos.jobs.enqueue(Job(job_id="b", run_id="r"))
    await repos.jobs.lease(worker_id="w1")

    stats = await repos.jobs.stats()
    assert stats.get(JobStatus.QUEUED) == 1
    assert stats.get(JobStatus.LEASED) == 1


# ---------------------------------------------------------------- 프로젝트 · 감사


async def test_라운드에_run_을_연결하고_해제한다(repos: Repos):
    """현행 tools.py 가 run 삭제 시 참조를 걷어내던 동작을 승계한다."""
    await repos.projects.create(Project(project_id="p1", name="효소 안정화"))
    await repos.rounds.create(Round(round_id="r1", project_id="p1", index=1))

    await repos.rounds.link_runs("r1", ["run-1", "run-2"])
    assert (await repos.rounds.get("r1")).linked_run_ids == ["run-1", "run-2"]

    #  같은 run 을 다시 넣어도 늘지 않는다
    await repos.rounds.link_runs("r1", ["run-1"])
    assert len((await repos.rounds.get("r1")).linked_run_ids) == 2

    await repos.rounds.unlink_run("run-1")
    assert (await repos.rounds.get("r1")).linked_run_ids == ["run-2"]


async def test_감사로그를_남기고_찾는다(repos: Repos):
    """감사 기록."""
    await repos.audit.record("model.register", actor_id="u1", target_type="model", target_id="rfd3:v1")
    await repos.audit.record("run.create", actor_id="u2", target_type="run", target_id="run-1")
    await repos.audit.record("model.register", actor_id="u2", target_type="model", target_id="af2:v1")

    assert len(await repos.audit.search(action="model.register")) == 2
    assert len(await repos.audit.search(actor_id="u2")) == 2
    assert len(await repos.audit.search(target_type="run")) == 1


async def test_보고서는_버전을_쌓는다(repos: Repos):
    """자동 생성본과 수동 수정본을 모두 보관한다."""
    await repos.reports.save(Report(report_id=new_id("rep"), run_id="run-x", body="초안"))
    await repos.reports.save(Report(
        report_id=new_id("rep"), run_id="run-x", body="수정본", generated_by="manual",
    ))

    latest = await repos.reports.latest("run-x")
    assert latest.version == 2
    assert latest.generated_by == "manual"
    assert len(await repos.reports.history("run-x")) == 2


async def test_피드백_데이터셋을_추출한다(repos: Repos):
    """후속 surrogate·ranking 모델 학습용."""
    await repos.feedback.add(Feedback(run_id="run-y", subject_id="s1", verdict="positive", score=0.9))
    await repos.feedback.add(Feedback(run_id="run-y", subject_id="s2", verdict="negative", score=0.1))

    rows = await repos.feedback.export_dataset()
    assert len(rows) == 2
    assert {r["subject_id"] for r in rows} == {"s1", "s2"}
