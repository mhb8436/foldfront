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
    Role,
    User,
    InputFile,
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

    async def claim_start(self, run_id: str) -> Run | None:
        """PENDING -> RUNNING, once. A second starter, or a cancel that landed
        in between, finds nothing to claim and gets None."""
        now = utcnow()
        doc = await self.col.find_one_and_update(
            {"run_id": run_id, "status": RunStatus.PENDING},
            {"$set": {"status": RunStatus.RUNNING, "started_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return Run(**_clean(doc)) if doc else None

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

    async def fork(self, run_id: str, *, from_stage: str | None = None,
                   keep_names: list[str] | None = None) -> Run | None:
        """Forking makes a new run. The original is never written to.

        With from_stage, everything before that stage is inherited and the run
        resumes from it - the point being to retry one stage without paying for
        the ones that already succeeded.
        """
        src = await self.get(run_id)
        if src is None:
            return None

        keep: list[StageState] = []
        if keep_names is not None:
            #  The service decided from the graph which stages the fork inherits.
            keep = [st for st in src.stages if st.name in keep_names]
        elif from_stage:
            names = [st.name for st in src.stages]
            if from_stage not in names:
                #  Found in review: an unknown name inherited every stage and the
                #  fork "succeeded" on start having run nothing - fabricated provenance.
                raise ValueError(f"그런 단계가 없습니다: {from_stage}")
            for st in src.stages:
                if st.name == from_stage:
                    break
                if st.status is not RunStatus.SUCCEEDED:
                    #  A stage inherited as RUNNING or FAILED blocks the fork point
                    #  for ever; nothing in the child can ever finish it.
                    raise ValueError(f"{st.name} 단계가 끝나지 않아 {from_stage} 부터 갈라질 수 없습니다")
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
            for _ in range(4):
                ev = RunEvent(
                    run_id=run_id, seq=int(counter["event_seq"]), level=level, stage=stage,
                    message=message, payload=payload or {},
                )
                try:
                    await self.col.insert_one(ev.model_dump())
                    return ev
                except DuplicateKeyError:
                    #  A run written before the counter existed - by earlier code
                    #  or by the migrator - has events 1..N and a counter that
                    #  just started at 1. Lift the counter to N and go again.
                    last = await self.col.find_one({"run_id": run_id}, sort=[("seq", DESCENDING)])
                    await self.db[C.RUNS].update_one(
                        {"run_id": run_id}, {"$max": {"event_seq": int(last["seq"]) if last else 0}}
                    )
                    counter = await self.db[C.RUNS].find_one_and_update(
                        {"run_id": run_id}, {"$inc": {"event_seq": 1}},
                        projection={"event_seq": 1}, return_document=ReturnDocument.AFTER,
                    )
            raise RuntimeError(f"run_events: seq contention on {run_id} did not settle")

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


class InputRepo(BaseRepo):
    """Uploaded inputs: who stored what, and which runs read it."""

    @property
    def col(self):
        return self.db[C.INPUTS]

    async def record(self, item: InputFile) -> InputFile:
        await self.col.insert_one(item.model_dump())
        return item

    async def usage(self, owner_id: str | None) -> int:
        """Bytes this owner holds, all of them.

        Counting only unread files made the allowance unbounded: a run that
        merely named an upload took it off the count for good. Everything
        counts; what is pinned by a run is listed as such so the person can
        see why the number does not fall.
        """
        rows = await self.col.aggregate([
            {"$match": {"owner_id": owner_id}},
            {"$group": {"_id": None, "bytes": {"$sum": "$size_bytes"}}},
        ]).to_list(length=1)
        return int(rows[0]["bytes"]) if rows else 0

    async def by_paths(self, paths: list[str]) -> list[InputFile]:
        if not paths:
            return []
        cur = self.col.find({"path": {"$in": paths}})
        return [InputFile(**_clean(d)) for d in await cur.to_list(length=len(paths))]

    async def link_run(self, paths: list[str], run_id: str) -> int:
        """Remember that a run read these. What a run read is kept with it."""
        if not paths:
            return 0
        res = await self.col.update_many(
            {"path": {"$in": paths}}, {"$addToSet": {"run_ids": run_id}, "$set": {"updated_at": utcnow()}},
        )
        return res.modified_count

    async def prunable(self, *, older_than: datetime, limit: int = 1000) -> list[InputFile]:
        """Old and unread by any run. A file a run read is that run's provenance."""
        cur = self.col.find(
            {"created_at": {"$lt": older_than}, "run_ids": {"$size": 0}}
        ).sort("created_at", ASCENDING).limit(limit)
        return [InputFile(**_clean(d)) for d in await cur.to_list(length=limit)]

    async def forget_if_unread(self, input_id: str) -> bool:
        """Remove the record only if still nothing has read it. A run that
        linked the file between the sweep's read and this is respected."""
        res = await self.col.delete_one({"input_id": input_id, "run_ids": {"$size": 0}})
        return res.deleted_count == 1

    async def list(self, owner_id: str | None, *, limit: int = 200) -> list[InputFile]:
        cur = self.col.find({"owner_id": owner_id}).sort("created_at", DESCENDING).limit(limit)
        return [InputFile(**_clean(d)) for d in await cur.to_list(length=limit)]


class UserRepo(BaseRepo):
    """Who has used the console, and what an operator has decided about them.

    The identity provider says who someone is and, coarsely, what they are -
    the original's tokens carry admin or user and nothing finer. What it
    cannot say is that this researcher is a viewer here, or that this account
    is a service account. That is an operator's decision, and it lives here.
    """

    @property
    def col(self):
        return self.db[C.USERS]

    async def get(self, user_id: str) -> User | None:
        d = await self.col.find_one({"user_id": user_id})
        return User(**_clean(d)) if d else None

    async def seen(self, user_id: str, *, subject: str | None, email: str | None,
                   roles: list[Role]) -> User:
        """Record a sign-in and return the account.

        The account is the provider's *subject*. The username is a label the
        provider may change and that two people may, at different times, share
        - so it is never what a record is found by. A subject seen before is
        that record, under whatever name it now carries; a name seen before
        under a different subject is somebody else's, and is refused rather
        than inherited.

        What an operator set is never overwritten by the provider, and the
        sign-in stamp is written at most once a minute rather than per request.
        """
        now = utcnow()
        stale = now - timedelta(seconds=60)
        subject = subject or None
        existing = await self.col.find_one({"subject": subject}) if subject else None
        if existing is None:
            same_name = await self.col.find_one({"user_id": user_id})
            if same_name is not None:
                if same_name.get("subject") and same_name.get("subject") != subject:
                    raise ValueError(f"'{user_id}' 는 다른 계정의 이름입니다")
                existing = same_name

        if existing is None:
            doc: dict[str, Any] = {
                "user_id": user_id, "roles": [str(r) for r in roles], "active": True,
                "created_at": now, "last_login_at": now, "updated_at": now, "email": email or None,
            }
            #  No subject key at all when there is none: the unique index is
            #  sparse, and an explicit null would still be indexed - and collide.
            if subject:
                doc["subject"] = subject
            try:
                await self.col.insert_one(doc)
            except DuplicateKeyError:
                #  A first visit fires several requests at once, and each of
                #  them reached here with no record. One insert won; this is
                #  the record it made.
                pass
            found = await self.col.find_one({"subject": subject}) if subject else None
            if found is None:
                found = await self.col.find_one({"user_id": user_id})
            if found is None:
                raise ValueError(f"'{user_id}' 계정을 기록하지 못했습니다")
            if found.get("subject") and subject and found["subject"] != subject:
                raise ValueError(f"'{user_id}' 는 다른 계정의 이름입니다")
            existing = found

        patch: dict[str, Any] = {}
        if subject and not existing.get("subject"):
            #  A record made from a token with no subject, now claimed by one
            #  that has it. From here on the subject is what finds it.
            patch["subject"] = subject
        if existing.get("user_id") != user_id:
            #  Renamed at the provider. Keep the record, take the new name -
            #  unless the name is already someone else's, in which case keep ours.
            taken = await self.col.find_one({"user_id": user_id, "_id": {"$ne": existing["_id"]}})
            if taken is None:
                patch["user_id"] = user_id
                #  Everything the person owns is keyed on the name; it follows.
                for col in (C.INPUTS, C.RUNS):
                    await self.db[col].update_many(
                        {"owner_id": existing["user_id"]}, {"$set": {"owner_id": user_id}},
                    )
        if existing.get("email") != (email or None):
            patch["email"] = email or None
        if not existing.get("last_login_at") or existing["last_login_at"] < stale:
            patch["last_login_at"] = now
        if patch:
            patch["updated_at"] = now
            d = await self.col.find_one_and_update(
                {"_id": existing["_id"]}, {"$set": patch}, return_document=ReturnDocument.AFTER,
            )
            return User(**_clean(d))
        return User(**_clean(existing))

    async def list(self, *, limit: int = 500) -> list[User]:
        cur = self.col.find({}).sort("last_login_at", DESCENDING).limit(limit)
        return [User(**_clean(d)) for d in await cur.to_list(length=limit)]

    async def set_roles(self, user_id: str, roles: list[Role]) -> User | None:
        d = await self.col.find_one_and_update(
            {"user_id": user_id},
            {"$set": {"roles": [str(r) for r in roles], "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        return User(**_clean(d)) if d else None

    async def set_active(self, user_id: str, active: bool) -> User | None:
        d = await self.col.find_one_and_update(
            {"user_id": user_id}, {"$set": {"active": active, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        return User(**_clean(d)) if d else None

    async def active_admins(self, *, exclude_subject: str | None = None) -> int:
        q: dict[str, Any] = {"active": True, "roles": str(Role.ADMIN)}
        if exclude_subject:
            q["subject"] = {"$ne": exclude_subject}
        return await self.col.count_documents(q)


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
        self.users = UserRepo(db)
        self.inputs = InputRepo(db)
