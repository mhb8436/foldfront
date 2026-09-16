"""모델 실행 어댑터.

라우팅이 정한 실행 위치(RunPod 엔드포인트 · HTTP 워커 · 컨테이너)로 실제 호출을 보낸다.
현행 RAPID 의 클라이언트 구현(`pipeline_mcp.clients`)을 승계하되, 호출 표면을 하나로 모아
워커가 모델 종류를 몰라도 되게 한다.

어댑터 규격 — `invoke(route, payload) -> dict`.
호출 결과는 조건식이 참조할 수 있도록 평평한 사전으로 돌려준다(의 분기 근거).

★ 모의 어댑터를 함께 둔다. 흐름 전체를 GPU 없이 돌려 보기 위해서다.
   다만 **모의 실행 결과를 「동작한다」로 기재하지 않는다** — 실증은 실제 엔드포인트로 한다.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from foldfront.engine.router import Route


class AdapterError(RuntimeError):
    """모델 호출이 실패했다."""


class Adapter(Protocol):
    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------- RunPod


@dataclass
class RunPodAdapter:
    """RunPod 서버리스 엔드포인트 호출.

    현행이 쓰는 방식이다. `/runsync` 는 완료까지 기다리고 `/run` 은 작업 ID 만 돌려준다.
    여기서는 작업 큐가 이미 비동기를 담당하므로 동기 호출을 쓴다.
    """

    api_key: str
    base_url: str = "https://api.runpod.ai/v2"
    timeout: float = 600.0

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AdapterError("RUNPOD_API_KEY 가 없다")

        url = f"{self.base_url}/{route.target}/runsync"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = min(route.timeout_seconds, self.timeout)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json={"input": payload}, headers=headers)
        except httpx.HTTPError as exc:
            raise AdapterError(f"RunPod 호출 실패: {exc}") from exc

        if resp.status_code >= 400:
            raise AdapterError(f"RunPod 오류 {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        status = str(body.get("status", "")).upper()
        if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise AdapterError(f"RunPod 작업 실패: {body.get('error') or status}")

        output = body.get("output")
        return output if isinstance(output, dict) else {"output": output}


# ---------------------------------------------------------------- HTTP 워커


@dataclass
class HttpAdapter:
    """자체 HTTP 워커 호출. 현행 SOLUPROT_URL·AF2_URL 방식을 승계한다."""

    timeout: float = 600.0

    async def invoke(self, route: Route, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = min(route.timeout_seconds, self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(route.target, json=payload)
        except httpx.HTTPError as exc:
            raise AdapterError(f"HTTP 워커 호출 실패: {exc}") from exc

        if resp.status_code >= 400:
            raise AdapterError(f"HTTP 워커 오류 {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        return body if isinstance(body, dict) else {"output": body}


# ---------------------------------------------------------------- 모의


@dataclass
class MockAdapter:
    """GPU 없이 흐름을 돌린다.

    ⚠️ 이것으로 얻은 결과는 **실증이 아니다.** 화면·엔진·큐가 이어져 도는지 확인하는 용도다.
    「동작한다」고 적을 근거는 실제 엔드포인트로 돌린 결과뿐이다.
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
        #  단계마다 뒤 조건식이 참조할 만한 지표를 낸다
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


# ---------------------------------------------------------------- 선택


class AdapterRegistry:
    """전송 방식별 어댑터를 고른다."""

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
