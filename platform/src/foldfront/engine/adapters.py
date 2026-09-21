"""Model execution adapters.

Sends the call to wherever routing decided - a RunPod endpoint, an HTTP
worker, a container. The original's clients are carried over, but behind one
calling surface, so a worker does not need to know what kind of model it is
running.

The contract is `invoke(route, payload) -> dict`, and the result comes back as
a flat dictionary because branch conditions read values out of it by path.

A mock adapter sits alongside the real ones so the whole flow can be exercised
without a GPU. Its output is not evidence that anything works: only a call to a
real endpoint is.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from foldfront.engine.router import Route


class AdapterError(RuntimeError):
    """The model call failed."""


class Adapter(Protocol):
    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------- RunPod


#  What RunPod calls a job that has not finished. `/runsync` answers with one
#  of these when its own wait runs out, which it does long before a cold
#  start finishes pulling a multi-gigabyte model image.
_PENDING = {"IN_QUEUE", "IN_PROGRESS"}
_FAILED = {"FAILED", "CANCELLED", "TIMED_OUT"}


@dataclass
class RunPodAdapter:
    """Calls a RunPod serverless endpoint, as the original does.

    `/runsync` does not wait for the result. It waits for about ninety
    seconds and then answers `{"id": ..., "status": "IN_QUEUE"}` with no
    output at all - which is most of the time on a first call, because the
    worker is still pulling the image. This adapter read that as a success
    and passed `{"output": None}` down the chain, where it became a stage
    that succeeded with no metrics. The mock adapter never produced that
    shape, so only a call to a real endpoint could find it.

    So: submit, and if the answer is not final, poll `/status/{id}` until it
    is. Polling here rather than in the worker because the worker has
    already leased this job - handing it back to the queue would mean paying
    for the cold start again.
    """

    api_key: str
    base_url: str = "https://api.runpod.ai/v2"
    timeout: float = 600.0
    poll_interval: float = 5.0

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AdapterError("RUNPOD_API_KEY 가 없습니다")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        budget = min(route.timeout_seconds, self.timeout)
        deadline = asyncio.get_running_loop().time() + budget

        async with httpx.AsyncClient(timeout=min(120.0, budget)) as client:
            body = await self._post(
                client, f"{self.base_url}/{route.target}/runsync",
                {"input": payload}, headers,
            )
            job_id = str(body.get("id") or "")

            while str(body.get("status", "")).upper() in _PENDING:
                if asyncio.get_running_loop().time() >= deadline:
                    #  Say which job, so it can be looked up or cancelled.
                    raise AdapterError(
                        f"RunPod 작업이 {budget:.0f}초 안에 끝나지 않았습니다"
                        f"{f' (job {job_id})' if job_id else ''}. "
                        "워커가 GPU 를 받지 못했거나 이미지를 아직 내려받는 중입니다"
                    )
                if not job_id:
                    raise AdapterError(f"RunPod 응답에 작업 식별자가 없습니다: {body}")
                await asyncio.sleep(self.poll_interval)
                body = await self._post(
                    client, f"{self.base_url}/{route.target}/status/{job_id}",
                    None, headers,
                )

        status = str(body.get("status", "")).upper()
        if status in _FAILED:
            raise AdapterError(f"RunPod 작업이 실패했습니다: {body.get('error') or status}")

        output = body.get("output")
        if output is None:
            #  Completed with nothing in it. Treated as a failure rather than
            #  an empty result, because every stage downstream reads the
            #  output and an empty one fails later, further from the cause.
            raise AdapterError(
                f"RunPod 작업이 {status or '알 수 없는 상태'} 로 끝났으나 결과가 비었습니다"
                f"{f' (job {job_id})' if job_id else ''}"
            )
        return output if isinstance(output, dict) else {"output": output}

    async def _post(
        self, client: httpx.AsyncClient, url: str,
        json_body: dict[str, Any] | None, headers: dict[str, str],
    ) -> dict[str, Any]:
        try:
            resp = await client.post(url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise AdapterError(f"RunPod 호출에 실패했습니다: {exc}") from exc
        if resp.status_code >= 400:
            raise AdapterError(f"RunPod 오류입니다 {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if not isinstance(body, dict):
            raise AdapterError(f"RunPod 응답이 객체가 아닙니다: {str(body)[:120]}")
        return body


# ---------------------------------------------------------------- HTTP worker


@dataclass
class HttpAdapter:
    """Calls a self-hosted HTTP worker, as SOLUPROT_URL and AF2_URL do."""

    timeout: float = 600.0

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = min(route.timeout_seconds, self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(route.target, json=payload)
        except httpx.HTTPError as exc:
            raise AdapterError(f"HTTP 워커 호출에 실패했습니다: {exc}") from exc

        if resp.status_code >= 400:
            raise AdapterError(f"HTTP 워커 오류입니다 {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        return body if isinstance(body, dict) else {"output": body}


# ---------------------------------------------------------------- mock


@dataclass
class MockAdapter:
    """Exercise the flow without a GPU.

    Nothing this returns is evidence. It shows that the console, the engine and
    the queue are connected; only a call to a real endpoint shows that a model
    ran.
    """

    seed: int = 20260916
    delay: float = 0.0
    fail_models: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if route.model_id in self.fail_models:
            raise AdapterError(f"모의 실패: {route.model_id}")

        rng = self._rng
        #  Emit the metrics a later branch condition would plausibly read
        table: dict[str, dict[str, Any]] = {
            "msa": {"depth": rng.randint(50, 500), "coverage": round(rng.uniform(0.6, 0.99), 3)},
            "rfd3": {"backbones": rng.randint(4, 12), "mean_rmsd": round(rng.uniform(0.8, 2.4), 3)},
            "bioemu": {"structures": rng.randint(10, 50)},
            "design": {"sequences": rng.randint(20, 120)},
            "soluprot": {
                "pass_rate": round(rng.uniform(0.15, 0.85), 3),
                "passed": rng.randint(3, 40),
            },
            "af2": {"plddt": round(rng.uniform(60.0, 95.0), 2), "rmsd": round(rng.uniform(0.5, 3.0), 2)},
            "novelty": {"novel": rng.randint(1, 20)},
            "diffdock": {"poses": rng.randint(5, 20), "best_score": round(rng.uniform(-9, -4), 2)},
        }
        result = dict(table.get(route.model_id, {"ok": True}))
        result["_mock"] = True
        result["_model"] = f"{route.model_id}:{route.version}"
        return result


# ---------------------------------------------------------------- selection


class AdapterRegistry:
    """Picks the adapter for a transport."""

    def __init__(
        self,
        *,
        runpod_api_key: str | None = None,
        mock: bool = False,
        mock_adapter: MockAdapter | None = None,
    ) -> None:
        self.mock = mock
        self._mock = mock_adapter or MockAdapter()
        self._runpod = RunPodAdapter(api_key=runpod_api_key or "")
        self._http = HttpAdapter()

    def pick(self, route: Route) -> Adapter:
        if self.mock:
            return self._mock
        if route.transport == "runpod":
            return self._runpod
        if route.transport == "http":
            return self._http
        raise AdapterError(
            f"{route.transport} 전송은 아직 직접 실행하지 않는다 "
            f"— 컨테이너 실행은 기관 쿠버네티스 연계로 처리한다"
        )

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.pick(route).invoke(route, payload)
