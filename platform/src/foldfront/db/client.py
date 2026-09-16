"""MongoDB 연결과 인덱스 정의.

컬렉션 이름은 한 곳에서만 정한다 — 오타로 빈 컬렉션이 생기는 것을 막는다.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from foldfront.core.config import get_settings


class C:
    """컬렉션 이름."""

    RUNS = "runs"
    RUN_EVENTS = "run_events"
    ARTIFACTS = "artifacts"
    MODELS = "model_versions"
    WORKFLOWS = "workflows"
    JOBS = "jobs"
    PROJECTS = "projects"
    ROUNDS = "rounds"
    TASKS = "tasks"
    FEEDBACK = "feedback"
    EXPERIMENTS = "experiments"
    REPORTS = "reports"
    AUDIT = "audit_logs"
    USERS = "users"


#  인덱스 — 조회 경로에 맞춘다.
#  이 동시 50명·3초 이내를 요구하므로 목록 조회가 전부 인덱스를 타야 한다.
INDEXES: dict[str, list[IndexModel]] = {
    C.RUNS: [
        IndexModel([("run_id", ASCENDING)], unique=True, name="uq_run_id"),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)], name="ix_status_created"),
        IndexModel([("project_id", ASCENDING), ("round_id", ASCENDING)], name="ix_project_round"),
        IndexModel([("owner_id", ASCENDING), ("created_at", DESCENDING)], name="ix_owner_created"),
        IndexModel([("forked_from_run_id", ASCENDING)], name="ix_fork_parent"),
        IndexModel([("workflow_id", ASCENDING)], name="ix_workflow"),
    ],
    C.RUN_EVENTS: [
        IndexModel([("run_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="uq_run_seq"),
        IndexModel([("run_id", ASCENDING), ("created_at", DESCENDING)], name="ix_run_created"),
    ],
    C.ARTIFACTS: [
        IndexModel([("run_id", ASCENDING), ("path", ASCENDING)], unique=True, name="uq_run_path"),
        IndexModel([("run_id", ASCENDING), ("stage", ASCENDING)], name="ix_run_stage"),
        IndexModel([("kind", ASCENDING)], name="ix_kind"),
        #  수명주기가 지난 중간 산출물을 찾는다
        IndexModel([("retain_until", ASCENDING)], name="ix_retain", sparse=True),
    ],
    C.MODELS: [
        IndexModel([("model_id", ASCENDING), ("version", ASCENDING)], unique=True, name="uq_model_version"),
        IndexModel([("model_id", ASCENDING), ("active", ASCENDING), ("is_default", DESCENDING)], name="ix_routing"),
        IndexModel([("kind", ASCENDING), ("active", ASCENDING)], name="ix_kind_active"),
        IndexModel([("approval_status", ASCENDING)], name="ix_approval"),
    ],
    C.WORKFLOWS: [
        IndexModel([("workflow_id", ASCENDING), ("version", DESCENDING)], unique=True, name="uq_workflow_version"),
        IndexModel([("is_template", ASCENDING), ("updated_at", DESCENDING)], name="ix_template"),
        IndexModel([("owner_id", ASCENDING)], name="ix_owner"),
        IndexModel([("project_id", ASCENDING)], name="ix_project", sparse=True),
    ],
    C.JOBS: [
        IndexModel([("job_id", ASCENDING)], unique=True, name="uq_job_id"),
        #  큐에서 꺼낼 때 쓰는 인덱스. 우선순위 높고 오래 기다린 것부터
        IndexModel(
            [("status", ASCENDING), ("priority", DESCENDING), ("queued_at", ASCENDING)],
            name="ix_dequeue",
        ),
        IndexModel([("run_id", ASCENDING)], name="ix_run"),
        #  만료된 lease 회수
        IndexModel([("status", ASCENDING), ("lease_expires_at", ASCENDING)], name="ix_lease"),
    ],
    C.PROJECTS: [
        IndexModel([("project_id", ASCENDING)], unique=True, name="uq_project_id"),
        IndexModel([("archived", ASCENDING), ("updated_at", DESCENDING)], name="ix_archived"),
    ],
    C.ROUNDS: [
        IndexModel([("round_id", ASCENDING)], unique=True, name="uq_round_id"),
        IndexModel([("project_id", ASCENDING), ("index", ASCENDING)], name="ix_project_index"),
    ],
    C.TASKS: [
        IndexModel([("task_id", ASCENDING)], unique=True, name="uq_task_id"),
        IndexModel([("project_id", ASCENDING), ("status", ASCENDING)], name="ix_project_status"),
    ],
    C.FEEDBACK: [
        IndexModel([("run_id", ASCENDING), ("created_at", DESCENDING)], name="ix_run_created"),
        IndexModel([("project_id", ASCENDING)], name="ix_project", sparse=True),
    ],
    C.EXPERIMENTS: [
        IndexModel([("run_id", ASCENDING), ("created_at", DESCENDING)], name="ix_run_created"),
        #  파생 데이터셋 추출 시 지표별로 훑는다
        IndexModel([("metric", ASCENDING), ("created_at", DESCENDING)], name="ix_metric", sparse=True),
    ],
    C.REPORTS: [
        IndexModel([("report_id", ASCENDING)], unique=True, name="uq_report_id"),
        IndexModel([("run_id", ASCENDING), ("version", DESCENDING)], name="ix_run_version"),
    ],
    C.AUDIT: [
        #  누가·무엇을·언제
        IndexModel([("created_at", DESCENDING)], name="ix_created"),
        IndexModel([("actor_id", ASCENDING), ("created_at", DESCENDING)], name="ix_actor"),
        IndexModel([("action", ASCENDING), ("created_at", DESCENDING)], name="ix_action"),
        IndexModel([("target_type", ASCENDING), ("target_id", ASCENDING)], name="ix_target"),
    ],
    C.USERS: [
        IndexModel([("user_id", ASCENDING)], unique=True, name="uq_user_id"),
        IndexModel([("subject", ASCENDING)], unique=True, sparse=True, name="uq_subject"),
        IndexModel([("email", ASCENDING)], sparse=True, name="ix_email"),
    ],
}


_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(get_settings().mongo_uri, tz_aware=True)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_settings().mongo_db]


async def ensure_indexes(db: AsyncIOMotorDatabase | None = None) -> dict[str, int]:
    """인덱스를 만든다. 기동 시 한 번 호출한다. 이미 있으면 아무 일도 하지 않는다."""
    db = db if db is not None else get_db()
    created: dict[str, int] = {}
    for name, models in INDEXES.items():
        if models:
            await db[name].create_indexes(models)
            created[name] = len(models)
    return created


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
