"""RunPod 어댑터 시험 — 실제 호출이 드러낸 것을 고정한다.

2026. 9. 21. 실제 엔드포인트를 처음 불러 보고서야 알았다. `/runsync` 는
결과를 기다려 주지 않는다. 90초쯤 기다리다 `{"id":…, "status":"IN_QUEUE"}`
를 돌려주고, 첫 호출은 워커가 이미지를 내려받느라 거의 항상 그렇게 된다.

어댑터는 그것을 성공으로 읽고 `{"output": None}` 을 내려보냈고, 단계는
「지표 없이 성공」으로 기록됐다. **모의 어댑터는 그 모양을 만들지 못한다.**
그래서 여기 시험으로 박아 둔다.
"""

from __future__ import annotations

import httpx
import pytest

from foldfront.engine.adapters import AdapterError, RunPodAdapter
from foldfront.engine.router import Route


def _route(timeout: int = 30) -> Route:
    return Route(model_id="proteinmpnn", version="v1", transport="runpod",
                 target="ep-test", resources=None, timeout_seconds=timeout)


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """어댑터가 만드는 AsyncClient 를 시험용으로 바꿔 끼운다."""
    holder: dict = {}

    real = httpx.AsyncClient

    def factory(**kwargs):
        if "handler" in holder:
            kwargs["transport"] = httpx.MockTransport(holder["handler"])
        return real(**kwargs)

    monkeypatch.setattr("foldfront.engine.adapters.httpx.AsyncClient", factory)
    return holder


async def test_키가_없으면_부르지_않는다():
    with pytest.raises(AdapterError, match="RUNPOD_API_KEY"):
        await RunPodAdapter(api_key="").invoke(_route(), {})


async def test_바로_끝나면_결과를_그대로_낸다(_patch_client):
    _patch_client["handler"] = lambda r: httpx.Response(
        200, json={"id": "j1", "status": "COMPLETED", "output": {"sequences": ["MKT"]}})

    out = await RunPodAdapter(api_key="k").invoke(_route(), {})

    assert out == {"sequences": ["MKT"]}


async def test_IN_QUEUE_를_성공으로_읽지_않는다(_patch_client):
    """이것이 실제 호출에서 드러난 결함이다.

    끝나지 않은 답을 성공으로 읽으면 단계가 「지표 없이 성공」으로 남고,
    실패는 한참 뒤 다른 자리에서 터진다.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/runsync"):
            return httpx.Response(200, json={"id": "j1", "status": "IN_QUEUE"})
        return httpx.Response(200, json={"id": "j1", "status": "COMPLETED",
                                         "output": {"sequences": ["MKT", "MRT"]}})

    _patch_client["handler"] = handler

    out = await RunPodAdapter(api_key="k", poll_interval=0.0).invoke(_route(), {})

    assert out == {"sequences": ["MKT", "MRT"]}
    assert any("/status/j1" in c for c in calls), "상태를 다시 묻지 않았다"


async def test_IN_PROGRESS_도_기다린다(_patch_client):
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runsync"):
            return httpx.Response(200, json={"id": "j2", "status": "IN_PROGRESS"})
        seen["n"] += 1
        if seen["n"] < 3:
            return httpx.Response(200, json={"id": "j2", "status": "IN_PROGRESS"})
        return httpx.Response(200, json={"id": "j2", "status": "COMPLETED", "output": {"ok": True}})

    _patch_client["handler"] = handler

    out = await RunPodAdapter(api_key="k", poll_interval=0.0).invoke(_route(), {})

    assert out == {"ok": True}
    assert seen["n"] == 3


async def test_끝났는데_결과가_비면_실패로_본다(_patch_client):
    """빈 결과를 통과시키면 실패가 한참 뒤 다른 자리에서 터진다."""
    _patch_client["handler"] = lambda r: httpx.Response(
        200, json={"id": "j3", "status": "COMPLETED", "output": None})

    with pytest.raises(AdapterError, match="결과가 비었습니다"):
        await RunPodAdapter(api_key="k").invoke(_route(), {})


async def test_실패_상태는_사유와_함께_올린다(_patch_client):
    _patch_client["handler"] = lambda r: httpx.Response(
        200, json={"id": "j4", "status": "FAILED", "error": "CUDA 메모리 부족"})

    with pytest.raises(AdapterError, match="CUDA 메모리 부족"):
        await RunPodAdapter(api_key="k").invoke(_route(), {})


async def test_시간_안에_안_끝나면_작업_식별자와_함께_알린다(_patch_client):
    """워커가 GPU 를 못 받으면 영영 큐에 있는다. 어느 작업인지 말해야 찾아 지운다."""
    _patch_client["handler"] = lambda r: httpx.Response(
        200, json={"id": "j5", "status": "IN_QUEUE"})

    with pytest.raises(AdapterError, match="j5"):
        await RunPodAdapter(api_key="k", poll_interval=0.0).invoke(_route(timeout=0), {})


async def test_HTTP_오류는_본문과_함께_올린다(_patch_client):
    _patch_client["handler"] = lambda r: httpx.Response(401, text="Unauthorized")

    with pytest.raises(AdapterError, match="401"):
        await RunPodAdapter(api_key="k").invoke(_route(), {})
