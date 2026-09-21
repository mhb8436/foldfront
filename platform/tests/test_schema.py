"""문서 스키마 시험.

MongoDB 없이 도는 부분과, 실제 MongoDB 가 있을 때만 도는 부분을 나눈다.
실제 연결 시험은 MONGO_URI 가 살아 있을 때만 수행한다.
"""

from __future__ import annotations

import os
import socket

import pytest

from foldfront.db.client import C, INDEXES
from foldfront.db.models import (
    Artifact,
    AuditLog,
    Feedback,
    Job,
    JobStatus,
    ModelKind,
    ModelVersion,
    NodeKind,
    ResourceSpec,
    Role,
    Run,
    RunEvent,
    RunStatus,
    StageState,
    User,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


needs_mongo = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


# ---------------------------------------------------------------- 스키마


def test_run_보존한다_현행_request_전체():
    """PipelineRequest 는 필드가 127개이고 계속 늘어난다.

    스키마를 고정하지 않고 통째로 보존해야 재현이 가능하다.
    """
    request = {"target_fasta": "seq.fasta", "rfd3_use": True, "conservation_tiers": [0.3, 0.5]}
    run = Run(run_id="run-0001", request=request, conservation_tiers=[0.3, 0.5])

    assert run.request == request
    assert run.status is RunStatus.PENDING
    #  조회용으로 승격한 값과 원본이 모두 남는다
    assert run.conservation_tiers == [0.3, 0.5]


def test_run_기록한다_fork_계보():
    """기본 동작이 fork 다. 어디서 갈라졌는지 남겨야 재현이 된다."""
    run = Run(run_id="run-0002", forked_from_run_id="run-0001", forked_from_stage="design")

    assert run.forked_from_run_id == "run-0001"
    assert run.forked_from_stage == "design"


def test_stage_기록한다_모델_버전():
    """어느 모델의 어느 버전으로 실행했는지 남지 않으면 재현이 불가능하다."""
    stage = StageState(
        name="design",
        status=RunStatus.SUCCEEDED,
        model_id="proteinmpnn",
        model_version="v1.0.1",
        endpoint_id="ep-abc",
        request_hash="sha256:...",
    )

    assert stage.model_version == "v1.0.1"
    assert stage.endpoint_id == "ep-abc"


def test_model_registry_담는다_요구_항목_전건():
    """모델ID·버전·컨테이너 이미지·엔드포인트·입출력 스키마·자원요구량·활성 상태."""
    mv = ModelVersion(
        model_id="rfd3",
        version="2026-09-01",
        kind=ModelKind.BACKBONE,
        endpoint_id="ep-rfd3",
        container_image="ghcr.io/example/rfd3:2026-09-01",
        input_schema={"type": "object", "required": ["contig"]},
        output_schema={"type": "object"},
        resources=ResourceSpec(gpu_count=1, gpu_memory_gb=24.0),
        weights_uri="s3://weights/rfd3/2026-09-01.pt",
        active=True,
        is_default=True,
    )

    assert mv.model_id == "rfd3"
    assert mv.resources.gpu_count == 1
    assert mv.container_image.endswith(":2026-09-01")
    assert mv.input_schema["required"] == ["contig"]
    #  기본값은 승인 완료. 사용자 정의 모델은 pending 으로 넣는다
    assert mv.approval_status == "approved"


def test_workflow_표현한다_병렬과_조건분기():
    """노드 조합·병렬 분기·조건 분기·사용자 정의 순서."""
    wf = Workflow(
        workflow_id="wf-binding",
        name="결합 예측",
        nodes=[
            WorkflowNode(node_id="n1", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="n2", kind=NodeKind.FANOUT),
            WorkflowNode(node_id="n3", kind=NodeKind.MODEL, model_id="rfd3"),
            WorkflowNode(node_id="n4", kind=NodeKind.MODEL, model_id="bioemu"),
            WorkflowNode(node_id="n5", kind=NodeKind.JOIN),
            WorkflowNode(node_id="n6", kind=NodeKind.BRANCH, condition="soluprot.pass_rate > 0.3"),
        ],
        edges=[
            WorkflowEdge(source="n1", target="n2"),
            WorkflowEdge(source="n2", target="n3"),
            WorkflowEdge(source="n2", target="n4"),
            WorkflowEdge(source="n3", target="n5"),
            WorkflowEdge(source="n4", target="n5"),
            WorkflowEdge(source="n5", target="n6"),
        ],
        is_template=True,
    )

    assert len(wf.nodes) == 6
    #  n2 에서 두 갈래로 나가는 것이 병렬 분기다
    assert len([e for e in wf.edges if e.source == "n2"]) == 2
    assert wf.version == 1


def test_workflow_노드는_버전을_생략할_수_있다():
    """버전을 비우면 라우터가 기본 활성 버전을 고른다."""
    node = WorkflowNode(node_id="n1", kind=NodeKind.MODEL, model_id="af2")

    assert node.model_version is None


def test_job_담는다_스케줄링_근거():
    """자원 요구량과 우선순위를 반영해 스케줄링한다."""
    job = Job(
        job_id="job-1",
        run_id="run-0001",
        node_id="n3",
        model_id="rfd3",
        priority=10,
        resources=ResourceSpec(gpu_count=1, gpu_memory_gb=24.0),
    )

    assert job.status is JobStatus.QUEUED
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.resources.gpu_count == 1


def test_artifact_는_메타만_담는다():
    """실체는 오브젝트 저장소에 둔다. PDB·MSA 는 문서 16MB 제한을 넘긴다."""
    art = Artifact(
        run_id="run-0001",
        stage="af2",
        path="af2/ranked_0.pdb",
        kind="pdb",
        size_bytes=1_048_576,
        checksum="sha256:abc",
    )

    assert art.user_visible is True
    assert "body" not in art.model_dump()


def test_audit_log_담는다_행위_주체와_대상():
    """로그인·실행 요청·모델 등록·설정 변경·다운로드."""
    log = AuditLog(
        actor_id="u-1",
        actor_role=Role.RESEARCHER,
        action="model.register",
        target_type="model",
        target_id="rfd3:2026-09-01",
        source_ip="10.0.0.1",
    )

    assert log.result == "success"
    assert log.action == "model.register"


def test_user_기본_역할은_조회_전용():
    """권한은 최소로 시작한다. 현행 admin·user 2단계를 4역할로 확장했다."""
    user = User(user_id="u-1", subject="oidc-sub-1")

    assert user.roles == [Role.VIEWER]
    assert Role.SERVICE in list(Role)


def test_feedback_와_experiment_는_run_에_붙는다():
    """현행 run 디렉토리의 feedback.jsonl · experiments.jsonl 을 승계한다."""
    fb = Feedback(run_id="run-0001", subject_id="seq-12", verdict="positive", score=0.8)
    assert fb.run_id == "run-0001"
    assert fb.verdict == "positive"


def test_run_event_는_순번을_가진다():
    """현행 events.jsonl 은 줄 순서가 곧 순번이다. DB 에서는 seq 로 보존한다."""
    ev = RunEvent(run_id="run-0001", seq=1, message="msa 시작")
    assert ev.level == "info"
    assert ev.seq == 1


# ---------------------------------------------------------------- 인덱스 정의


def test_인덱스가_모든_컬렉션에_정의되어_있다():
    """동시 50명·3초 이내를 요구하므로 목록 조회가 전부 인덱스를 타야 한다."""
    declared = {
        C.RUNS, C.RUN_EVENTS, C.ARTIFACTS, C.MODELS, C.WORKFLOWS, C.JOBS,
        C.PROJECTS, C.ROUNDS, C.TASKS, C.FEEDBACK, C.EXPERIMENTS,
        C.REPORTS, C.AUDIT, C.USERS, C.INPUTS, C.EVIDENCE,
    }

    assert set(INDEXES) == declared
    assert all(models for models in INDEXES.values())


def test_고유키가_중복_실행을_막는다():
    def names(coll: str) -> set[str]:
        return {m.document["name"] for m in INDEXES[coll]}

    assert "uq_run_id" in names(C.RUNS)
    assert "uq_model_version" in names(C.MODELS)
    assert "uq_workflow_version" in names(C.WORKFLOWS)
    assert "uq_run_seq" in names(C.RUN_EVENTS)
    #  같은 run 에 같은 경로의 아티팩트가 두 번 들어가지 않는다
    assert "uq_run_path" in names(C.ARTIFACTS)


def test_큐_인덱스가_있다():
    """status · priority · queued_at 복합 인덱스로 꺼낸다."""
    dequeue = [m for m in INDEXES[C.JOBS] if m.document["name"] == "ix_dequeue"]

    assert len(dequeue) == 1
    keys = list(dequeue[0].document["key"].items())
    assert keys == [("status", 1), ("priority", -1), ("queued_at", 1)]


# ---------------------------------------------------------------- 실제 연결


@needs_mongo
async def test_실제_mongodb_에_인덱스를_만든다():
    """실제 MongoDB 에 붙어 인덱스가 생성되는지 확인한다."""
    from foldfront.db.client import ensure_indexes, get_client

    os.environ.setdefault("MONGO_DB", "foldfront_test")
    created = await ensure_indexes()

    assert created[C.RUNS] == len(INDEXES[C.RUNS])
    get_client().close()
