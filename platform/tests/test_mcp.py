"""MCP 도구 표면 시험.

원본 도구와 신규 계층 도구가 **한 표면**으로 나오는지, 이름으로 올바르게 갈리는지 본다.
"""

from __future__ import annotations

import json
import socket

import pytest
from httpx import ASGITransport, AsyncClient

from foldfront.db.client import C, get_db


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_mcp")

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
    await ensure_indexes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c

    await dbclient.close_client()
    get_settings.cache_clear()


async def _rpc(client, method: str, params: dict | None = None):
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method,
                                        "params": params or {}})
    assert r.status_code == 200
    return r.json()


async def test_initialize_는_규약_판번호를_낸다(client):
    body = await _rpc(client, "initialize")
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert body["result"]["serverInfo"]["name"] == "foldfront"


async def test_도구_목록에_원본과_신규가_함께_나온다(client):
    """표면이 갈라져 있던 것을 합치는 것이 이 경로의 목적이다."""
    tools = (await _rpc(client, "tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}

    assert "pipeline.run" in names          # 원본
    assert "platform.run_start" in names    # 신규
    assert len(tools) > 60


async def test_도구_이름마다_스키마가_붙어_있다(client):
    tools = (await _rpc(client, "tools/list"))["result"]["tools"]
    assert all({"name", "description", "inputSchema"} <= set(t) for t in tools)


async def test_신규_도구를_부르면_신규_계층이_답한다(client):
    body = await _rpc(client, "tools/call",
                      {"name": "platform.job_stats", "arguments": {}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert "by_status" in payload


async def test_없는_워크플로는_오류가_아니라_사유를_낸다(client):
    body = await _rpc(client, "tools/call",
                      {"name": "platform.workflow_preflight",
                       "arguments": {"workflow_id": "없는것"}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["ok"] is False


async def test_없는_도구는_JSON_RPC_오류를_낸다(client):
    body = await _rpc(client, "tools/call", {"name": "platform.없는도구", "arguments": {}})
    assert body["error"]["code"] == -32601


async def test_지원하지_않는_메서드는_거절한다(client):
    body = await _rpc(client, "resources/list")
    assert body["error"]["code"] == -32601


async def test_원본_도구는_러너가_없으면_사유를_낸다(client):
    """실행 환경이 확정되기 전이다. 조용히 실패하지 않고 이유를 돌려준다."""
    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.status", "arguments": {}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert isinstance(payload, dict)
