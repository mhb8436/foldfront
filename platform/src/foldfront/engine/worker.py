"""The job worker.

Takes work off the queue, calls the model, reports the result.

    lease -> invoke -> complete_node -> (the next node is enqueued)

A worker holds no state. Killed and restarted, it picks up from the database.
A lease that expires lets another worker take the same job.

Any number of workers may run at once: the lease is taken atomically, so two
of them cannot claim the same job.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field

from foldfront.db.models import JobStatus
from foldfront.db.repositories import Repos
from foldfront.engine.adapters import AdapterError, AdapterRegistry
from foldfront.engine.payloads import PayloadError, build_payload
from foldfront.engine.router import Route
from foldfront.engine.service import ExecutionService

log = logging.getLogger("foldfront.worker")


@dataclass
class WorkerStats:
    leased: int = 0
    succeeded: int = 0
    failed: int = 0
    idle_polls: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "leased": self.leased, "succeeded": self.succeeded,
            "failed": self.failed, "idle_polls": self.idle_polls,
        }


@dataclass
class Worker:
    """Takes one job and runs it."""

    repos: Repos
    adapters: AdapterRegistry
    worker_id: str = field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:8]}")
    lease_seconds: int = 900
    max_gpu: int | None = None
    poll_interval: float = 1.0
    stats: WorkerStats = field(default_factory=WorkerStats)

    def __post_init__(self) -> None:
        self.service = ExecutionService(self.repos)

    async def step(self) -> bool:
        """Run one job. True if something ran, False if the queue was empty."""
        job = await self.repos.jobs.lease(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            max_gpu=self.max_gpu,
        )
        if job is None:
            self.stats.idle_polls += 1
            return False

        self.stats.leased += 1
        await self.repos.jobs.col.update_one(
            {"job_id": job.job_id}, {"$set": {"status": JobStatus.RUNNING}}
        )

        route_data = job.payload.get("route") or {}
        try:
            if not route_data:
                raise AdapterError(f"작업에 라우팅 정보가 없습니다: {job.job_id}")

            route = Route(
                model_id=route_data["model_id"],
                version=route_data["version"],
                transport=route_data["transport"],
                target=route_data["target"],
                resources=job.resources,
                timeout_seconds=route_data.get("timeout_seconds", 21600.0),
            )
            #  Inputs are shaped the way the original clients send them.
            #  The mock adapter ignores shape, so a failure to build one must
            #  not stop a demonstration before real endpoints are connected.
            params = dict(job.payload.get("params") or {})
            try:
                payload = build_payload(route.model_id, params, job.payload.get("upstream"))
            except PayloadError as exc:
                if not self.adapters.mock:
                    raise
                log.debug("skipping payload build (mock run): %s", exc)
                payload = params

            payload["run_id"] = job.run_id
            payload["node_id"] = job.node_id

            result = await self.adapters.invoke(route, payload)

        except (AdapterError, KeyError, Exception) as exc:  # a worker never dies
            self.stats.failed += 1
            await self.repos.jobs.finish(job.job_id, status=JobStatus.FAILED, error=str(exc))
            if job.node_id:
                await self.service.complete_node(
                    job.run_id, job.node_id, succeeded=False, error=str(exc)
                )
            log.warning("job failed %s: %s", job.job_id, exc)
            return True

        self.stats.succeeded += 1
        await self.repos.jobs.finish(job.job_id, status=JobStatus.SUCCEEDED)
        if job.node_id:
            await self.service.complete_node(
                job.run_id, job.node_id, succeeded=True, result=result
            )
        return True

    async def drain(self, *, max_steps: int = 1000) -> WorkerStats:
        """Run until the queue is empty. For tests and batch processing."""
        for _ in range(max_steps):
            if not await self.step():
                break
        return self.stats

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """The production loop: sleep briefly on an empty queue, then look again."""
        stop = stop or asyncio.Event()
        log.info("worker started %s", self.worker_id)
        while not stop.is_set():
            try:
                worked = await self.step()
            except Exception:  # an unexpected error must not stop the worker
                log.exception("error in worker loop")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
        log.info("worker stopped %s %s", self.worker_id, self.stats.as_dict())


async def run_workers(
    repos: Repos,
    *,
    count: int = 2,
    mock: bool = False,
    max_gpu: int | None = None,
    stop: asyncio.Event | None = None,
) -> list[Worker]:
    """Run several workers at once.

    Nothing coordinates them; the atomic lease is what keeps two from taking
    the same job.
    """
    adapters = AdapterRegistry(
        runpod_api_key=os.environ.get("RUNPOD_API_KEY"), mock=mock
    )
    workers = [
        Worker(repos=repos, adapters=adapters, max_gpu=max_gpu) for _ in range(count)
    ]
    stop = stop or asyncio.Event()
    await asyncio.gather(*(w.run_forever(stop=stop) for w in workers))
    return workers


async def reclaim_loop(
    repos: Repos, *, interval: float = 60.0, stop: asyncio.Event | None = None
) -> None:
    """Reclaim expired leases on a timer.

    A worker that dies leaves its job LEASED. Without this loop that job is
    locked for good and nothing reports it.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            n = await repos.jobs.reclaim_expired()
            if n:
                log.info("reclaimed %d expired jobs", n)
        except Exception:
            log.exception("error in reclaim loop")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
