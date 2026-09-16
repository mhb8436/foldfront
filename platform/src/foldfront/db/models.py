"""MongoDB 문서 스키마.

담는 것:
  Run 기준 설계 이력
  입력·파라미터·모델 버전 메타데이터 저장  Model Registry 저장소
  대용량 아티팩트 저장소 (메타데이터는 여기, 실체는 오브젝트 저장소)
  비교·랭킹·피드백·실험 데이터셋 축적
  보고서·감사이력 아카이빙  MongoDB 기반 메타데이터·작업 데이터 저장소
  자유형 DAG Workflow (workflows · workflow_runs)
  프로젝트·라운드·태스크·피드백 관리
  작업(Job) 큐 관리 및 스케줄링
  사용자 작업 데이터 체계화 및 버전 관리
  감사 로그 및 추적성

현행 RAPID v1.0.29 는 run 디렉토리에 request.json · status.json · events.jsonl 로 저장한다.
이 스키마는 그 구조를 보존하면서 MongoDB 로 옮긴다 — 필드 이름을 임의로 바꾸지 않는 것이
레거시 마이그레이션의 전제다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Doc(BaseModel):
    """모든 문서의 공통 기반."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- 실행 (Run)


class RunStatus(StrEnum):
    """현행 status.json 의 상태값을 그대로 승계한다."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageName(StrEnum):
    """현행 고정 단계 체인. 자유형 DAG 는 이 값에 매이지 않는다."""

    MSA = "msa"
    RFD3 = "rfd3"
    BIOEMU = "bioemu"
    DESIGN = "design"
    SOLUPROT = "soluprot"
    AF2 = "af2"
    NOVELTY = "novelty"


class StageState(Doc):
    """단계별 실행 상태. 체크포인트 재실행 단위다."""

    name: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #  어느 모델의 어느 버전으로 실행했는지 남긴다
    model_id: str | None = None
    model_version: str | None = None
    endpoint_id: str | None = None
    #  단계 요청 해시. 현행의 stage-specific request hash 를 승계한다
    request_hash: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class Run(Doc):
    """실행 1건. 현행 run 디렉토리 1개에 대응한다.

    현행 request.json 전체를 `request` 에 그대로 담는다. PipelineRequest 는 필드가 108개이고
    계속 늘어나므로 스키마를 고정하지 않는다 — 대신 조회에 쓰는 값만 위로 끌어올린다.
    """

    run_id: str
    status: RunStatus = RunStatus.PENDING
    mode: Literal["pipeline", "workflow", "binding"] = "pipeline"

    #  프로젝트·라운드에 연결한다. 현행 PipelineRequest 에도 같은 필드가 있다
    project_id: str | None = None
    round_id: str | None = None

    #  재현에 필요한 최소 정보. 현행 request.json 을 통째로 보존한다
    request: dict[str, Any] = Field(default_factory=dict)

    #  조회·필터에 쓰는 값만 승격한다 (전체는 request 안에 그대로 있다)
    target_fasta_name: str | None = None
    design_chains: list[str] = Field(default_factory=list)
    conservation_tiers: list[float] = Field(default_factory=list)

    stages: list[StageState] = Field(default_factory=list)

    #  fork 계보. 어느 run 의 어느 단계에서 갈라져 나왔는지
    forked_from_run_id: str | None = None
    forked_from_stage: str | None = None

    #  DAG 로 실행한 경우 그 정의를 가리킨다
    workflow_id: str | None = None
    workflow_version: int | None = None

    #  실행 환경 스냅샷 (환경변수 값이 아니라 버전·엔드포인트 식별자만)
    environment: dict[str, str] = Field(default_factory=dict)

    started_at: datetime | None = None
    finished_at: datetime | None = None
    owner_id: str | None = None
    error: str | None = None


class RunEvent(Doc):
    """현행 events.jsonl 1줄에 대응한다. 추가만 하고 수정하지 않는다."""

    run_id: str
    seq: int
    level: Literal["debug", "info", "warning", "error"] = "info"
    stage: str | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 아티팩트


class Artifact(Doc):
    """산출물 메타데이터.

    실체 파일은 오브젝트 저장소에 두고 여기에는 위치와 특성만 저장한다 —
    PDB·MSA·리포트는 단일 문서 16MB 제한을 넘기 쉽다.
    """

    run_id: str
    stage: str | None = None
    path: str  # 저장소 기준 상대 경로. 현행 run 디렉토리 구조를 승계한다
    kind: str  # pdb · fasta · a3m · json · svg · tsv · report · log
    size_bytes: int = 0
    checksum: str | None = None
    content_type: str | None = None
    #  사용자에게 보일 산출물인지. 현행 _is_user_visible_artifact_path 를 승계한다
    user_visible: bool = True
    #  수명주기 정책. 만료된 중간 산출물을 정리한다
    retain_until: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- Model Registry


class ModelKind(StrEnum):
    BACKBONE = "backbone"        # RFD3 · BioEmu
    SEQUENCE = "sequence"        # ProteinMPNN
    SOLUBILITY = "solubility"    # SoluProt
    STRUCTURE = "structure"      # AF2 · ColabFold
    DOCKING = "docking"          # DiffDock
    MSA = "msa"                  # MMseqs2
    EMBEDDING = "embedding"      # ESM
    OTHER = "other"


class ResourceSpec(BaseModel):
    """자원 요구량. 큐 스케줄링의 근거가 된다."""

    gpu_count: int = 0
    gpu_memory_gb: float | None = None
    cpu_count: int | None = None
    memory_gb: float | None = None
    timeout_seconds: float = 21600.0


class ModelVersion(Doc):
    """모델 1개 버전.

    현행 model_providers.py 는 엔드포인트 base_url·timeout·scope 만 관리한다.
    여기에 버전·컨테이너 이미지·입출력 스키마·자원 요구량을 더해 Registry 로 확장한다.
    """

    model_id: str          # rfd3 · proteinmpnn · af2 …  URL 이 아니라 고유 ID 로 부른다
    version: str           # v1.0.29 · 2026-09-01 …
    kind: ModelKind = ModelKind.OTHER
    display_name: str | None = None

    #  실행 위치 — 셋 중 하나 이상
    endpoint_id: str | None = None      # RunPod 서버리스 엔드포인트
    base_url: str | None = None         # 자체 HTTP 워커
    container_image: str | None = None  # 컨테이너 이미지 (태그 포함)

    #  입출력 스키마. 라우터가 요청을 검증하는 근거다
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    resources: ResourceSpec = Field(default_factory=ResourceSpec)

    #  활성화·비활성화. 비활성 버전은 라우팅 대상에서 빠진다
    active: bool = True
    #  같은 model_id 안에서 기본으로 선택될 버전
    is_default: bool = False

    #  사용자 정의 모델 등록 승인·검증·롤백
    approval_status: Literal["approved", "pending", "rejected"] = "approved"
    approved_by: str | None = None
    approved_at: datetime | None = None
    registered_by: str | None = None

    #  가중치 파일 위치 ( 「가중치」)
    weights_uri: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------- DAG 워크플로


class NodeKind(StrEnum):
    MODEL = "model"          # Registry 의 모델을 실행한다
    TRANSFORM = "transform"  # 내장 변환 (필터·선별·병합)
    BRANCH = "branch"        # 조건 분기
    FANOUT = "fanout"        # 병렬 분기
    JOIN = "join"            # 병렬 수렴


class WorkflowNode(BaseModel):
    """DAG 노드 1개."""

    node_id: str
    kind: NodeKind = NodeKind.MODEL
    label: str | None = None

    #  kind=MODEL 일 때 — 버전을 비우면 기본 버전으로 라우팅한다
    model_id: str | None = None
    model_version: str | None = None

    params: dict[str, Any] = Field(default_factory=dict)

    #  kind=BRANCH 일 때 — 참이면 on_true, 거짓이면 on_false 로 간다
    condition: str | None = None

    #  화면 배치 좌표. 편집기가 쓴다
    position: dict[str, float] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    source: str
    target: str
    #  BRANCH 노드에서 나가는 간선의 분기 조건
    branch: Literal["true", "false"] | None = None


class Workflow(Doc):
    """DAG 정의. 템플릿으로 저장하고 재사용한다.

    version 을 올리며 이력을 남긴다 — 같은 workflow_id 의 문서가 버전마다 하나씩 쌓인다.
    """

    workflow_id: str
    version: int = 1
    name: str
    description: str | None = None

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)

    #  템플릿이면 목록에 노출하고 복제해서 쓴다
    is_template: bool = False
    #  정형 Stage 체인을 DAG 로 표현한 기본 템플릿인지
    is_builtin: bool = False

    owner_id: str | None = None
    project_id: str | None = None

    #  사용자 정의 워크플로를 운영에 반영할 때의 승인 상태
    approval_status: Literal["approved", "pending", "rejected"] = "approved"


# ---------------------------------------------------------------- 작업 큐


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Doc):
    """작업 큐 항목.

    모델별 자원 요구량과 우선순위를 반영해 스케줄링한다.
    lease 방식으로 꺼내 가므로 워커가 죽어도 lease_expires_at 이 지나면 회수된다.
    """

    job_id: str
    run_id: str
    node_id: str | None = None   # DAG 실행이면 어느 노드인지
    stage: str | None = None     # 정형 실행이면 어느 단계인지

    status: JobStatus = JobStatus.QUEUED
    priority: int = 0            # 클수록 먼저

    model_id: str | None = None
    model_version: str | None = None
    resources: ResourceSpec = Field(default_factory=ResourceSpec)

    payload: dict[str, Any] = Field(default_factory=dict)

    attempts: int = 0
    max_attempts: int = 3
    leased_by: str | None = None
    lease_expires_at: datetime | None = None

    queued_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


# ---------------------------------------------------------------- 프로젝트 체계


class Project(Doc):
    """현행 workspace/projects/<id>/project.json 에 대응한다."""

    project_id: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    archived: bool = False
    tags: list[str] = Field(default_factory=list)


class Round(Doc):
    """현행 projects/<id>/rounds/<round_id>.json 에 대응한다."""

    round_id: str
    project_id: str
    name: str | None = None
    index: int = 1
    linked_run_ids: list[str] = Field(default_factory=list)
    objective: str | None = None
    archived: bool = False


class Task(Doc):
    """라운드 안의 작업 단위."""

    task_id: str
    project_id: str
    round_id: str | None = None
    title: str
    status: Literal["todo", "doing", "done", "dropped"] = "todo"
    assignee_id: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class Feedback(Doc):
    """현행 run 디렉토리의 feedback.jsonl 에 대응한다."""

    run_id: str
    project_id: str | None = None
    #  평가 대상 후보 서열·구조 식별자
    subject_id: str | None = None
    verdict: Literal["positive", "negative", "neutral"] = "neutral"
    score: float | None = None
    comment: str | None = None
    author_id: str | None = None


class Experiment(Doc):
    """현행 experiments.jsonl 에 대응한다. 습식 실험 결과를 연결한다."""

    run_id: str
    project_id: str | None = None
    subject_id: str | None = None
    metric: str | None = None          # activity · expression · tm …
    value: float | None = None
    unit: str | None = None
    protocol: str | None = None
    note: str | None = None
    author_id: str | None = None


# ---------------------------------------------------------------- 보고서 · 감사


class Report(Doc):
    """자동 생성 보고서와 수동 수정본을 함께 보관한다."""

    report_id: str
    run_id: str
    language: Literal["ko", "en"] = "ko"
    format: Literal["markdown", "html", "pdf"] = "markdown"
    version: int = 1
    #  본문이 크면 artifact 로 빼고 여기에는 참조만 남긴다
    body: str | None = None
    artifact_path: str | None = None
    generated_by: Literal["auto", "manual"] = "auto"
    author_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class AuditLog(Doc):
    """감사 로그. 추가만 하고 수정·삭제하지 않는다.

    대상 행위 — 로그인 · 실행 요청 · 모델 등록 · 설정 변경 · 운영 패치 · 결과 다운로드.
    """

    actor_id: str | None = None
    actor_role: str | None = None
    action: str                     # run.create · model.register · config.update …
    target_type: str | None = None  # run · model · workflow · config
    target_id: str | None = None
    result: Literal["success", "failure", "denied"] = "success"
    source_ip: str | None = None
    user_agent: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 사용자 · 권한


class Role(StrEnum):
    """현행은 admin·user 2단계뿐이라 4역할로 확장한다."""

    ADMIN = "admin"            # 운영·모델 등록·설정 변경
    RESEARCHER = "researcher"  # 실행·분석·보고서
    VIEWER = "viewer"          # 조회 전용
    SERVICE = "service"        # 외부 연계 계정 (MCP·API)


class User(Doc):
    user_id: str
    subject: str | None = None   # OIDC sub. 현행 oidc.py 의 claims 를 승계한다
    email: str | None = None
    display_name: str | None = None
    roles: list[Role] = Field(default_factory=lambda: [Role.VIEWER])
    active: bool = True
    last_login_at: datetime | None = None
