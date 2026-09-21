"""외부 GPU 관제 시험.

RunPod 자격 증명이 없는 기계에서 돈다. 그래서 여기서 보는 것은 관제가
**되는지**가 아니라, 안 될 때 **왜 안 되는지 제대로 말하는지**다. 그것이
운영자에게 필요한 답이고, 지금 우리가 정직하게 확인할 수 있는 전부다.
"""

from __future__ import annotations

import socket

import pytest
from httpx import ASGITransport, AsyncClient

from foldfront.db.client import C, get_db
from foldfront.db.models import Role


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_gpu")
    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)

    from foldfront.core.config import get_settings
    from foldfront.db import client as dbclient

    get_settings.cache_clear()
    await dbclient.close_client()

    from foldfront.api.app import app
    from foldfront.db.client import ensure_indexes

    db = get_db()
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await dbclient.close_client()
    get_settings.cache_clear()


async def _become(client, *roles: Role) -> None:
    """개발 계정의 역할을 바꾼다.

    한 번은 요청을 보내야 한다. 계정 기록은 첫 요청에서 생기고, 없는 계정에
    역할을 지정하면 아무 일도 일어나지 않는다.
    """
    from foldfront.db.repositories import Repos

    await client.get("/api/v1/gpu/status")
    await Repos().users.set_roles("dev", list(roles))


async def test_자격증명이_없으면_없다고_답한다(client):
    """비어 있는 화면과 「설정하지 않았다」는 다른 말이다."""
    body = (await client.get("/api/v1/gpu/status")).json()

    assert body["configured"] is False
    assert body["reachable"] is False
    assert "RUNPOD_API_KEY" in body["reason"]


async def test_상태_조회는_망을_건드리지_않는다(client):
    """자격 증명이 없다는 답에 시간이 걸릴 이유가 없다."""
    import time

    began = time.monotonic()
    await client.get("/api/v1/gpu/status")

    assert time.monotonic() - began < 1.0


async def test_자격증명이_없으면_관제_경로는_503(client):
    """404 도 500 도 아니다. 설정하면 되는 일이라고 말해야 한다."""
    for path in ("/api/v1/gpu/endpoints", "/api/v1/gpu/endpoints/ep-1",
                 "/api/v1/gpu/history?endpoint_id=ep-1"):
        r = await client.get(path)
        assert r.status_code == 503, path
        assert r.json()["error"]["code"] == "gpu.not_configured"


async def test_비용은_운영자만_본다(client):
    """비용은 운영자의 일이지 모든 읽는 이의 일이 아니다."""
    await _become(client, Role.RESEARCHER)

    assert (await client.get("/api/v1/gpu/billing")).status_code == 403


async def test_엔드포인트_변경은_운영자만_한다(client):
    await _become(client, Role.RESEARCHER)

    r = await client.post("/api/v1/gpu/endpoints/ep-1", json={"workersMax": 3})

    assert r.status_code == 403


async def test_아무_역할이_없으면_상태도_못_본다(client):
    """읽기 경로도 누군가이긴 해야 한다."""
    await client.get("/api/v1/gpu/status")
    await get_db()[C.USERS].update_one({"user_id": "dev"}, {"$set": {"roles": []}})

    assert (await client.get("/api/v1/gpu/status")).status_code == 403


async def test_관제_경로가_명세에_실린다(client):
    """화면을 붙이기 전에 계약이 먼저 있어야 한다."""
    spec = (await client.get("/openapi.json")).json()

    for path in ("/api/v1/gpu/status", "/api/v1/gpu/endpoints",
                 "/api/v1/gpu/billing", "/api/v1/gpu/history"):
        assert path in spec["paths"], path
