"""작업 워커.

큐에서 작업을 꺼내 모델을 호출하고 결과를 보고한다.

    lease → invoke → complete_node → (다음 노드가 큐에 들어간다)

워커는 상태를 들고 있지 않는다. 죽었다 살아나도 DB 에서 이어서 돈다.
lease 가 만료되면 다른 워커가 같은 작업을 가져간다.

여러 워커를 동시에 띄울 수 있다 — lease 가 원자적이라 같은 작업을 두 번 집지 않는다.
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
    """작업 하나를 꺼내 처리한다."""

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
        """작업 하나를 처리한다. 처리했으면 참, 큐가 비었으면 거짓."""
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
                raise AdapterError(f"작업에 라우팅 정보가 없다: {job.job_id}")

            route = Route(
                model_id=route_data["model_id"],
                version=route_data["version"],
                transport=route_data["transport"],
                target=route_data["target"],
                resources=job.resources,
                timeout_seconds=route_data.get("timeout_seconds", 21600.0),
            )
            #  모델별 입력은 원본 클라이언트가 쓰던 모양으로 만든다(~007).
            #  모의 어댑터는 입력 형태를 보지 않으므로 구성 실패가 실행을 막지 않게 한다 —
            #  실제 엔드포인트에 붙기 전까지 시연이 끊기면 안 된다.
            params = dict(job.payload.get("params") or {})
            try:
                payload = build_payload(route.model_id, params, job.payload.get("upstream"))
            except PayloadError as exc:
                if not self.adapters.mock:
                    raise
                log.debug("입력 구성을 건너뛴다(모의 실행): %s", exc)
                payload = params

            payload["run_id"] = job.run_id
            payload["node_id"] = job.node_id

            result = await self.adapters.invoke(route, payload)

        except (AdapterError, KeyError, Exception) as exc:  # 워커는 죽지 않는다
            self.stats.failed += 1
            await self.repos.jobs.finish(job.job_id, status=JobStatus.FAILED, error=str(exc))
            if job.node_id:
                await self.service.complete_node(
                    job.run_id, job.node_id, succeeded=False, error=str(exc)
                )
            log.warning("작업 실패 %s: %s", job.job_id, exc)
            return True

        self.stats.succeeded += 1
        await self.repos.jobs.finish(job.job_id, status=JobStatus.SUCCEEDED)
        if job.node_id:
            await self.service.complete_node(
                job.run_id, job.node_id, succeeded=True, result=result
            )
        return True

    async def drain(self, *, max_steps: int = 1000) -> WorkerStats:
        """큐가 빌 때까지 돈다. 시험과 일괄 처리에 쓴다."""
        for _ in range(max_steps):
            if not await self.step():
                break
        return self.stats

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """운영용 반복. 큐가 비면 잠시 쉬고 다시 본다."""
        stop = stop or asyncio.Event()
        log.info("워커 시작 %s", self.worker_id)
        while not stop.is_set():
            try:
                worked = await self.step()
            except Exception:  # 예상 못한 오류로 워커가 멈추지 않게 한다
                log.exception("워커 순환 중 오류")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
        log.info("워커 종료 %s · %s", self.worker_id, self.stats.as_dict())


async def run_workers(
    repos: Repos,
    *,
    count: int = 2,
    mock: bool = False,
    max_gpu: int | None = None,
    stop: asyncio.Event | None = None,
) -> list[Worker]:
    """워커 여러 개를 동시에 띄운다.

    같은 작업을 두 번 집지 않는 것은 lease 의 원자성이 보장한다.
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
    """만료된 lease 를 주기적으로 회수한다.

    워커가 죽으면 그 작업이 LEASED 로 남는다. 이 순환이 없으면 영원히 잠긴다.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            n = await repos.jobs.reclaim_expired()
            if n:
                log.info("만료된 작업 %d건을 회수했다", n)
        except Exception:
            log.exception("회수 순환 중 오류")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
