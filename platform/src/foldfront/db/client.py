"""MongoDB connection and index definitions.

Collection names are declared once. A typo elsewhere would otherwise create an
empty collection and read nothing from it, with no error to show for it.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import OperationFailure
from pymongo import ASCENDING, DESCENDING, IndexModel

from foldfront.db.models import utcnow
from foldfront.core.config import get_settings


log = logging.getLogger(__name__)


class C:
    """Collection names."""

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
    INPUTS = "inputs"


#  Indexes follow the paths the console actually queries by.
#  Every list view has to hit an index: the console polls them continuously,
#  and a collection scan under that load is what takes a cluster down.
INDEXES: dict[str, list[IndexModel]] = {
    C.INPUTS: [
        IndexModel([("input_id", ASCENDING)], unique=True, name="uq_input_id"),
        #  Usage per owner, and what to prune: oldest first
        IndexModel([("owner_id", ASCENDING), ("created_at", ASCENDING)], name="ix_owner_created"),
        IndexModel([("path", ASCENDING)], unique=True, name="uq_path"),
    ],
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
        #  Finds intermediate artifacts whose retention has passed
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
        #  The order the queue pops in: highest priority, longest waiting
        IndexModel(
            [("status", ASCENDING), ("priority", DESCENDING), ("queued_at", ASCENDING)],
            name="ix_dequeue",
        ),
        IndexModel([("run_id", ASCENDING)], name="ix_run"),
        #  One live job per node of a run. Two writers can both decide a node is
        #  ready; this makes the second insert fail instead of running the
        #  model twice. Fixed-chain jobs carry no node_id and are left out.
        IndexModel(
            [("run_id", ASCENDING), ("node_id", ASCENDING)],
            unique=True, name="uq_run_node_active",
            partialFilterExpression={
                "node_id": {"$type": "string"},
                "status": {"$in": ["queued", "leased", "running"]},
            },
        ),
        #  Reclaims leases that expired
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
        #  Scanned by metric when a derived dataset is pulled
        IndexModel([("metric", ASCENDING), ("created_at", DESCENDING)], name="ix_metric", sparse=True),
    ],
    C.REPORTS: [
        IndexModel([("report_id", ASCENDING)], unique=True, name="uq_report_id"),
        IndexModel([("run_id", ASCENDING), ("version", DESCENDING)], name="ix_run_version"),
    ],
    C.AUDIT: [
        #  Who did what, and when
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
    """Create the indexes. Called once at startup; a no-op if they exist."""
    db = db if db is not None else get_db()
    created: dict[str, int] = {}
    for name, models in INDEXES.items():
        if not models:
            continue
        try:
            await db[name].create_indexes(models)
        except OperationFailure as exc:
            if name != C.JOBS or exc.code != 11000:
                raise
            #  The unique index on live (run, node) cannot be built over a
            #  store that already holds the twins it exists to prevent. Keep
            #  the earliest of each pair, cancel the rest, build again.
            await _cancel_twin_jobs(db)
            await db[name].create_indexes(models)
        created[name] = len(models)
    return created


async def _cancel_twin_jobs(db: AsyncIOMotorDatabase) -> int:
    live = {"$in": ["queued", "leased", "running"]}
    groups = await db[C.JOBS].aggregate([
        {"$match": {"status": live, "node_id": {"$type": "string"}}},
        {"$sort": {"queued_at": 1}},
        {"$group": {"_id": {"run_id": "$run_id", "node_id": "$node_id"},
                    "ids": {"$push": "$job_id"}}},
        {"$match": {"ids.1": {"$exists": True}}},
    ]).to_list(length=10_000)
    cancelled = 0
    for g in groups:
        extra = g["ids"][1:]
        res = await db[C.JOBS].update_many(
            {"job_id": {"$in": extra}},
            {"$set": {"status": "cancelled", "error": "같은 노드의 작업이 이미 있어 취소", "updated_at": utcnow()}},
        )
        cancelled += res.modified_count
        log.warning("cancelled %d twin job(s) for %s/%s: %s",
                    len(extra), g["_id"]["run_id"], g["_id"]["node_id"], extra)
    return cancelled


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
