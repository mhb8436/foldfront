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


@dataclass
class RunPodAdapter:
    """Calls a RunPod serverless endpoint, as the original does.

    `/runsync` waits for the result; `/run` returns a job id to poll. The
    synchronous one is right here because the queue already provides the
    asynchrony - polling inside a worker that is itself a queue consumer would
    only add a second mechanism doing the same thing.
    """

    api_key: str
    base_url: str = "https://api.runpod.ai/v2"
    timeout: float = 600.0

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AdapterError("RUNPOD_API_KEY 가 없습니다")

        url = f"{self.base_url}/{route.target}/runsync"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = min(route.timeout_seconds, self.timeout)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json={"input": payload}, headers=headers)
        except httpx.HTTPError as exc:
            raise AdapterError(f"RunPod 호출에 실패했습니다: {exc}") from exc

        if resp.status_code >= 400:
            raise AdapterError(f"RunPod 오류입니다 {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        status = str(body.get("status", "")).upper()
        if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise AdapterError(f"RunPod 작업이 실패했습니다: {body.get('error') or status}")

        output = body.get("output")
        return output if isinstance(output, dict) else {"output": output}


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
