"""The repository layer.

Every read and write against MongoDB lives here. Nothing above this names a
collection or writes a query, which is what keeps a schema change to one file.

Every update touches updated_at. Times are stored in UTC and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from foldfront.db.client import C, get_db
from foldfront.db.models import (
    Artifact,
    AuditLog,
    Job,
    JobStatus,
    ModelVersion,
    Project,
    Report,
    Round,
    Run,
    RunEvent,
    RunStatus,
    StageState,
    Workflow,
    utcnow,
)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _clean(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop _id. Above this layer, documents are known by their own ids."""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


class BaseRepo:
    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self.db = db if db is not None else get_db()


# ---------------------------------------------------------------- runs


class RunRepo(BaseRepo):
    """Run history. A forked run lives here beside the one it came from."""

    @property
    def col(self):
        return self.db[C.RUNS]

    async def create(self, run: Run) -> Run:
        await self.col.insert_one(run.model_dump())
        return run

    async def get(self, run_id: str) -> Run | None:
        doc = _clean(await self.col.find_one({"run_id": run_id}))
        return Run(**doc) if doc else None

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        project_id: str | None = None,
        round_id: str | None = None,
        owner_id: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[Run]:
        q: dict[str, Any] = {}
        if status:
            q["status"] = status
        if project_id:
            q["project_id"] = project_id
        if round_id:
            q["round_id"] = round_id
        if owner_id:
            q["owner_id"] = owner_id
        cur = self.col.find(q).sort("created_at", DESCENDING).skip(skip).limit(limit)
        return [Run(**_clean(d)) for d in await cur.to_list(length=limit)]

    async def count(self, **q: Any) -> int:
        return await self.col.count_documents({k: v for k, v in q.items() if v is not None})

    async def set_status(
        self, run_id: str, status: RunStatus, *, error: str | None = None
    ) -> Run | None:
        patch: dict[str, Any] = {"status": status, "updated_at": utcnow()}
        if status is RunStatus.RUNNING:
            patch["started_at"] = utcnow()
        if status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            patch["finished_at"] = utcnow()
        if error is not None:
            patch["error"] = error
        doc = await self.col.find_one_and_update(
            {"run_id": run_id}, {"$set": patch}, return_document=ReturnDocument.AFTER
        )
        return Run(**_clean(doc)) if doc else None

    async def upsert_stage(self, run_id: str, stage: StageState) -> Run | None:
        """Update a stage, appending it if this is the first time."""
        existing = await self.col.find_one(
            {"run_id": run_id, "stages.name": stage.name}, {"_id": 1}
        )
        if existing:
            doc = await self.col.find_one_and_update(
                {"run_id": run_id, "stages.name": stage.name},
                {"$set": {"stages.$": stage.model_dump(), "updated_at": utcnow()}},
                return_document=ReturnDocument.AFTER,
            )
        else:
            doc = await self.col.find_one_and_update(
                {"run_id": run_id},
                {"$push": {"stages": stage.model_dump()}, "$set": {"updated_at": utcnow()}},
                return_document=ReturnDocument.AFTER,
            )
        return Run(**_clean(doc)) if doc else None

    async def fork(self, run_id: str, *, from_stage: str | None = None) -> Run | None:
        """Forking makes a new run. The original is never written to.

        With from_stage, everything before that stage is inherited and the run
        resumes from it - the point being to retry one stage without paying for
        the ones that already succeeded.
        """
        src = await self.get(run_id)
        if src is None:
            return None

        keep: list[StageState] = []
        if from_stage:
            for st in src.stages:
                if st.name == from_stage:
                    break
                keep.append(st)

        child = Run(
            run_id=new_id("run"),
            mode=src.mode,
            project_id=src.project_id,
            round_id=src.round_id,
            request=dict(src.request),
            target_fasta_name=src.target_fasta_name,
            design_chains=list(src.design_chains),
            conservation_tiers=list(src.conservation_tiers),
            stages=keep,
            forked_from_run_id=src.run_id,
            forked_from_stage=from_stage,
            workflow_id=src.workflow_id,
            workflow_version=src.workflow_version,
            environment=dict(src.environment),
            owner_id=src.owner_id,
        )
        return await self.create(child)


class RunEventRepo(BaseRepo):
    """The original events.jsonl. Append-only."""

    @property
    def col(self):
        return self.db[C.RUN_EVENTS]

    async def append(
        self, run_id: str, message: str, *, stage: str | None = None,
        level: str = "info", payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        #  The number comes from an atomic $inc on the run document, so two
        #  workers finishing parallel nodes of one run can never draw the same
        #  one. It used to be read-max-then-insert, which collided under the
        #  unique index about once in thirty and left the run unclosed.
        counter = await self.db[C.RUNS].find_one_and_update(
            {"run_id": run_id},
            {"$inc": {"event_seq": 1}},
            projection={"event_seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if counter is not None:
            ev = RunEvent(
                run_id=run_id, seq=int(counter["event_seq"]), level=level, stage=stage,
                message=message, payload=payload or {},
            )
            await self.col.insert_one(ev.model_dump())
            return ev

        #  No run document to count on - events written on their own, as some
        #  tests do. Read-then-insert with a bounded retry is enough there,
        #  because nothing else is writing.
        for _ in range(16):
            last = await self.col.find_one({"run_id": run_id}, sort=[("seq", DESCENDING)])
            seq = (last["seq"] + 1) if last else 1
            ev = RunEvent(
                run_id=run_id, seq=seq, level=level, stage=stage,
                message=message, payload=payload or {},
            )
            try:
                await self.col.insert_one(ev.model_dump())
                return ev
            except DuplicateKeyError:
                continue
        raise RuntimeError(f"run_events: seq contention on {run_id} did not settle")

    async def list(self, run_id: str, *, limit: int = 200) -> list[RunEvent]:
        cur = self.col.find({"run_id": run_id}).sort("seq", ASCENDING).limit(limit)
        return [RunEvent(**_clean(d)) for d in await cur.to_list(length=limit)]


class ArtifactRepo(BaseRepo):
    """Metadata only. The files themselves live in object storage."""

    @property
    def col(self):
        return self.db[C.ARTIFACTS]

    async def register(self, art: Artifact) -> Artifact:
        await self.col.update_one(
            {"run_id": art.run_id, "path": art.path},
            {"$set": art.model_dump()},
            upsert=True,
        )
        return art

    async def get(self, run_id: str, path: str) -> Artifact | None:
        doc = await self.col.find_one({"run_id": run_id, "path": path})
        return Artifact(**_clean(doc)) if doc else None

    async def list(
        self, run_id: str, *, stage: str | None = None, user_visible: bool | None = None
    ) -> list[Artifact]:
        q: dict[str, Any] = {"run_id": run_id}
        if stage:
            q["stage"] = stage
        if user_visible is not None:
            q["user_visible"] = user_visible
        cur = self.col.find(q).sort("path", ASCENDING)
        return [Artifact(**_clean(d)) for d in await cur.to_list(length=2000)]

    async def expired(self, *, now: datetime | None = None) -> list[Artifact]:
        """Find intermediate artifacts whose retention has passed."""
        cur = self.col.find({"retain_until": {"$ne": None, "$lt": now or utcnow()}})
        return [Artifact(**_clean(d)) for d in await cur.to_list(length=1000)]


# ---------------------------------------------------------------- Model Registry


class ModelRepo(BaseRepo):
    """The model registry: ids, versions and availability, read by routing."""

    @property
    def col(self):
        return self.db[C.MODELS]

    async def register(self, mv: ModelVersion) -> ModelVersion:
        """Register a model version, replacing one with the same id and version."""
        if mv.is_default:
            await self.col.update_many(
                {"model_id": mv.model_id}, {"$set": {"is_default": False}}
            )
        await self.col.update_one(
            {"model_id": mv.model_id, "version": mv.version},
            {"$set": mv.model_dump()},
            upsert=True,
        )
        return mv

    async def get(self, model_id: str, version: str) -> ModelVersion | None:
        doc = _clean(await self.col.find_one({"model_id": model_id, "version": version}))
        return ModelVersion(**doc) if doc else None

    async def resolve(self, model_id: str, version: str | None = None) -> ModelVersion | None:
        """What dynamic routing turns on.

        A named version is taken as given. Without one, the active default is
        chosen, falling back to the most recently registered active version
        when nothing is marked default. An unapproved version is never chosen,
        whatever else matches.
        """
        q: dict[str, Any] = {"model_id": model_id, "approval_status": "approved"}
        if version:
            q["version"] = version
            doc = _clean(await self.col.find_one(q))
            return ModelVersion(**doc) if doc else None

        q["active"] = True
        doc = _clean(
            await self.col.find_one(q, sort=[("is_default", DESCENDING), ("created_at", DESCENDING)])
        )
        return ModelVersion(**doc) if doc else None

    async def list(
        self, *, model_id: str | None = None, kind: str | None = None,
        active_only: bool = False, limit: int = 200,
    ) -> list[ModelVersion]:
        q: dict[str, Any] = {}
        if model_id:
            q["model_id"] = model_id
        if kind:
            q["kind"] = kind
        if active_only:
            q["active"] = True
        cur = self.col.find(q).sort([("model_id", ASCENDING), ("version", DESCENDING)]).limit(limit)
        return [ModelVersion(**_clean(d)) for d in await cur.to_list(length=limit)]

    async def set_active(self, model_id: str, version: str, active: bool) -> ModelVersion | None:
        doc = await self.col.find_one_and_update(
            {"model_id": model_id, "version": version},
            {"$set": {"active": active, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        return ModelVersion(**_clean(doc)) if doc else None

    async def approve(
        self, model_id: str, version: str, *, approved_by: str, decision: str = "approved"
    ) -> ModelVersion | None:
        """Approve, reject or roll back a model someone registered."""
        doc = await self.col.find_one_and_update(
            {"model_id": model_id, "version": version},
            {"$set": {
                "approval_status": decision,
                "approved_by": approved_by,
                "approved_at": utcnow(),
                "updated_at": utcnow(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        return ModelVersion(**_clean(doc)) if doc else None


# ---------------------------------------------------------------- workflows


class WorkflowRepo(BaseRepo):
    """Versions accumulate; nothing is overwritten."""

    @property
    def col(self):
        return self.db[C.WORKFLOWS]

    async def save(self, wf: Workflow) -> Workflow:
        """Save as a new version, leaving earlier ones in place."""
        last = await self.col.find_one(
            {"workflow_id": wf.workflow_id}, sort=[("version", DESCENDING)]
        )
        wf.version = (last["version"] + 1) if last else 1
        wf.updated_at = utcnow()
        await self.col.insert_one(wf.model_dump())
        return wf

    async def get(self, workflow_id: str, version: int | None = None) -> Workflow | None:
        q: dict[str, Any] = {"workflow_id": workflow_id}
        if version is not None:
            q["version"] = version
            doc = _clean(await self.col.find_one(q))
        else:
            doc = _clean(await self.col.find_one(q, sort=[("version", DESCENDING)]))
        return Workflow(**doc) if doc else None

    async def list(
        self, *, templates_only: bool = False, project_id: str | None = None, limit: int = 100
    ) -> list[Workflow]:
        """Latest version of each workflow_id.

        `project_id` means "this project's, and the shared ones" rather than
        "this project's only". The built-in templates carry no project, and a
        project that could not see them would start with an empty studio.
        """
        match: dict[str, Any] = {}
        if templates_only:
            match["is_template"] = True
        if project_id:
            match["$or"] = [{"project_id": project_id}, {"project_id": None}]
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {"$sort": {"workflow_id": 1, "version": -1}},
            {"$group": {"_id": "$workflow_id", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$sort": {"updated_at": -1}},
            {"$limit": limit},
        ]
        return [
            Workflow(**_clean(d))
            for d in await self.col.aggregate(pipeline).to_list(length=limit)
        ]

    async def versions(self, workflow_id: str) -> list[int]:
        cur = self.col.find({"workflow_id": workflow_id}, {"version": 1}).sort("version", ASCENDING)
        return [d["version"] for d in await cur.to_list(length=500)]


# ---------------------------------------------------------------- job queue


class JobRepo(BaseRepo):
    """Lease-based. A worker that dies releases its job when the lease ends."""

    @property
    def col(self):
        return self.db[C.JOBS]

    async def enqueue(self, job: Job) -> Job | None:
        """Queue a job, or return None if this node already has a live one.

        The partial unique index on (run_id, node_id) for active jobs is what
        makes this safe with several writers: a worker finishing a node and a
        reconcile pass on the same run can both decide the next node is ready,
        and only one insert can win.
        """
        try:
            await self.col.insert_one(job.model_dump())
        except DuplicateKeyError:
            return None
        return job

    async def lease(
        self, *, worker_id: str, lease_seconds: int = 900, model_id: str | None = None,
        max_gpu: int | None = None,
    ) -> Job | None:
        """Take one job: highest priority, longest waiting.

        find_one_and_update is atomic, which is the whole reason several
        workers can run without coordinating. Reading and then writing would
        let two of them claim the same job.
        """
        q: dict[str, Any] = {"status": JobStatus.QUEUED}
        if model_id:
            q["model_id"] = model_id
        if max_gpu is not None:
            q["resources.gpu_count"] = {"$lte": max_gpu}

        doc = await self.col.find_one_and_update(
            q,
            {"$set": {
                "status": JobStatus.LEASED,
                "leased_by": worker_id,
                "lease_expires_at": utcnow() + timedelta(seconds=lease_seconds),
                "updated_at": utcnow(),
            }, "$inc": {"attempts": 1}},
            sort=[("priority", DESCENDING), ("queued_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return Job(**_clean(doc)) if doc else None

    async def finish(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> Job | None:
        doc = await self.col.find_one_and_update(
            {"job_id": job_id},
            {"$set": {
                "status": status, "error": error,
                #  None when nothing was given, so a reply that was genuinely
                #  {} can be told apart from a result that was never recorded.
                "result": dict(result) if result is not None else None,
                "finished_at": utcnow(), "updated_at": utcnow(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        return Job(**_clean(doc)) if doc else None

    async def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """Return expired leases to the queue, failing those past their retry limit."""
        now = now or utcnow()
        cur = self.col.find(
            {"status": {"$in": [JobStatus.LEASED, JobStatus.RUNNING]},
             "lease_expires_at": {"$ne": None, "$lt": now}}
        )
        reclaimed = 0
        for d in await cur.to_list(length=500):
            exhausted = d.get("attempts", 0) >= d.get("max_attempts", 3)
            await self.col.update_one(
                {"job_id": d["job_id"]},
                {"$set": {
                    "status": JobStatus.FAILED if exhausted else JobStatus.QUEUED,
                    "leased_by": None,
                    "lease_expires_at": None,
                    "error": "lease 만료" if exhausted else None,
                    "updated_at": now,
                }},
            )
            reclaimed += 1
        return reclaimed

    async def stats(self) -> dict[str, int]:
        """Queue depth, for the operations screen."""
        pipeline = [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]
        rows = await self.col.aggregate(pipeline).to_list(length=20)
        return {r["_id"]: r["n"] for r in rows}

    async def leased(self) -> list[Job]:
        """Jobs a worker currently holds. What is actually occupying GPUs."""
        cur = self.col.find({"status": JobStatus.LEASED})
        return [Job(**_clean(d)) for d in await cur.to_list(length=500)]

    async def list_for_run(self, run_id: str) -> list[Job]:
        cur = self.col.find({"run_id": run_id}).sort("queued_at", ASCENDING)
        return [Job(**_clean(d)) for d in await cur.to_list(length=500)]


# ---------------------------------------------------------------- projects


class ProjectRepo(BaseRepo):
    """Projects and rounds: runs grouped the way research is."""

    @property
    def col(self):
        return self.db[C.PROJECTS]

    async def create(self, p: Project) -> Project:
        await self.col.insert_one(p.model_dump())
        return p

    async def get(self, project_id: str) -> Project | None:
        doc = _clean(await self.col.find_one({"project_id": project_id}))
        return Project(**doc) if doc else None

    async def list(self, *, include_archived: bool = False, limit: int = 100) -> list[Project]:
        q: dict[str, Any] = {} if include_archived else {"archived": False}
        cur = self.col.find(q).sort("updated_at", DESCENDING).limit(limit)
        return [Project(**_clean(d)) for d in await cur.to_list(length=limit)]

    async def archive(self, project_id: str, archived: bool = True) -> Project | None:
        doc = await self.col.find_one_and_update(
            {"project_id": project_id},
            {"$set": {"archived": archived, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        return Project(**_clean(doc)) if doc else None


class RoundRepo(BaseRepo):
    @property
    def col(self):
        return self.db[C.ROUNDS]

    async def create(self, r: Round) -> Round:
        await self.col.insert_one(r.model_dump())
        return r

    async def get(self, round_id: str) -> Round | None:
        doc = _clean(await self.col.find_one({"round_id": round_id}))
        return Round(**doc) if doc else None

    async def list(self, project_id: str) -> list[Round]:
        cur = self.col.find({"project_id": project_id}).sort("index", ASCENDING)
        return [Round(**_clean(d)) for d in await cur.to_list(length=500)]

    async def link_runs(self, round_id: str, run_ids: Sequence[str]) -> Round | None:
        doc = await self.col.find_one_and_update(
            {"round_id": round_id},
            {"$addToSet": {"linked_run_ids": {"$each": list(run_ids)}},
             "$set": {"updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        return Round(**_clean(doc)) if doc else None

    async def unlink_run(self, run_id: str) -> int:
        """Detach a deleted run from its round, as the original tools.py does."""
        res = await self.col.update_many(
            {"linked_run_ids": run_id},
            {"$pull": {"linked_run_ids": run_id}, "$set": {"updated_at": utcnow()}},
        )
        return res.modified_count


class RecordRepo(BaseRepo):
    """Feedback and experiments. Same shape, so one class covers both."""

    def __init__(self, collection: str, db: AsyncIOMotorDatabase | None = None) -> None:
        super().__init__(db)
        self.name = collection

    @property
    def col(self):
        return self.db[self.name]

    async def add(self, doc: Any) -> Any:
        await self.col.insert_one(doc.model_dump())
        return doc

    async def list(self, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        cur = self.col.find({"run_id": run_id}).sort("created_at", DESCENDING).limit(limit)
        return [_clean(d) for d in await cur.to_list(length=limit)]

    async def export_dataset(
        self, *, metric: str | None = None, limit: int = 10000
    ) -> list[dict[str, Any]]:
        """Pull a dataset for training surrogate and ranking models."""
        q: dict[str, Any] = {}
        if metric:
            q["metric"] = metric
        cur = self.col.find(q).sort("created_at", ASCENDING).limit(limit)
        return [_clean(d) for d in await cur.to_list(length=limit)]


# ---------------------------------------------------------------- reports, audit


class ReportRepo(BaseRepo):
    """Reports. Revisions accumulate against the same run."""

    @property
    def col(self):
        return self.db[C.REPORTS]

    async def save(self, rep: Report) -> Report:
        last = await self.col.find_one({"run_id": rep.run_id}, sort=[("version", DESCENDING)])
        rep.version = (last["version"] + 1) if last else 1
        await self.col.insert_one(rep.model_dump())
        return rep

    async def latest(self, run_id: str, *, language: str | None = None) -> Report | None:
        q: dict[str, Any] = {"run_id": run_id}
        if language:
            q["language"] = language
        doc = _clean(await self.col.find_one(q, sort=[("version", DESCENDING)]))
        return Report(**doc) if doc else None

    async def history(self, run_id: str) -> list[Report]:
        cur = self.col.find({"run_id": run_id}).sort("version", ASCENDING)
        return [Report(**_clean(d)) for d in await cur.to_list(length=200)]


class AuditRepo(BaseRepo):
    """Append-only. No path here updates or deletes."""

    @property
    def col(self):
        return self.db[C.AUDIT]

    async def record(
        self, action: str, *, actor_id: str | None = None, actor_role: str | None = None,
        target_type: str | None = None, target_id: str | None = None,
        result: str = "success", source_ip: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditLog:
        log = AuditLog(
            actor_id=actor_id, actor_role=actor_role, action=action,
            target_type=target_type, target_id=target_id, result=result,
            source_ip=source_ip, detail=detail or {},
        )
        await self.col.insert_one(log.model_dump())
        return log

    async def search(
        self, *, actor_id: str | None = None, action: str | None = None,
        target_type: str | None = None, target_id: str | None = None,
        since: datetime | None = None, limit: int = 200,
    ) -> list[AuditLog]:
        q: dict[str, Any] = {}
        if actor_id:
            q["actor_id"] = actor_id
        if action:
            q["action"] = action
        if target_type:
            q["target_type"] = target_type
        if target_id:
            q["target_id"] = target_id
        if since:
            q["created_at"] = {"$gte": since}
        cur = self.col.find(q).sort("created_at", DESCENDING).limit(limit)
        return [AuditLog(**_clean(d)) for d in await cur.to_list(length=limit)]


# ---------------------------------------------------------------- bundle


class Repos:
    """All repositories in one object, so callers carry a single handle."""

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        db = db if db is not None else get_db()
        self.db = db
        self.runs = RunRepo(db)
        self.events = RunEventRepo(db)
        self.artifacts = ArtifactRepo(db)
        self.models = ModelRepo(db)
        self.workflows = WorkflowRepo(db)
        self.jobs = JobRepo(db)
        self.projects = ProjectRepo(db)
        self.rounds = RoundRepo(db)
        self.feedback = RecordRepo(C.FEEDBACK, db)
        self.experiments = RecordRepo(C.EXPERIMENTS, db)
        self.reports = ReportRepo(db)
        self.audit = AuditRepo(db)
