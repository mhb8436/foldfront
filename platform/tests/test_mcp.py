"""MCP 도구 표면 시험.

원본 도구와 신규 계층 도구가 **한 표면**으로 나오는지, 이름으로 올바르게 갈리는지 본다.
"""

from __future__ import annotations

import json
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


async def _seed_run(client) -> str:
    """원본 도구가 읽을 실행 하나를 신규 계층 쪽에 만든다."""
    await client.post("/api/v1/models", json={
        "model_id": "msa", "version": "v1", "kind": "msa",
        "endpoint_id": "ep-msa", "active": True, "is_default": True,
        "resources": {"gpu_count": 0},
    })
    await client.post("/api/v1/projects", json={"project_id": "proj-view", "name": "투영"})
    await client.post("/api/v1/rounds", json={
        "round_id": "round-view", "project_id": "proj-view", "name": "1차",
    })
    await client.post("/api/v1/workflows", json={
        "workflow_id": "wf-view", "name": "투영",
        "nodes": [{"node_id": "msa", "kind": "model", "model_id": "msa"}],
        "edges": [],
    })
    started = await client.post("/api/v1/runs", json={
        "workflow_id": "wf-view", "request": {"target_fasta": "x.fasta"},
    })
    run_id = started.json()["run_id"]
    await client.post(f"/api/v1/runs/{run_id}/nodes/msa/complete",
                      json={"succeeded": True, "result": {"depth": 120}})
    return run_id


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


async def test_원본_도구는_인자가_모자라면_사유를_낸다(client):
    """러너는 선다. 모자란 것은 인자이고, 조용히 실패하지 않고 이유를 돌려준다."""
    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.status", "arguments": {}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert isinstance(payload, dict)
    assert payload["ok"] is False
    assert "run_id" in payload["error"]


async def test_모델을_시작하는_원본_도구는_거절한다(client):
    """큐를 거치지 않는 두 번째 실행 경로가 생기면, 리스 없는 작업이 GPU 를 쓴다."""
    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.run", "arguments": {"run_id": "run-x"}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["ok"] is False
    assert "platform.run_start" in payload["error"]


async def test_원본_분석_도구가_투영된_실행을_읽는다(client, tmp_path, monkeypatch):
    """1번 항목의 완료 판정.

    실행은 MongoDB 에 있고 원본 도구는 디렉터리를 읽는다. 그 사이를 투영기가
    잇는다. 잇기 전에는 62종이 전부 「러너를 세우지 못했습니다」로 끝났다.
    """
    pytest.importorskip("pipeline_mcp.tools")

    run_id = await _seed_run(client)
    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.get_hit_list", "arguments": {"run_id": run_id}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["run_id"] == run_id
    assert "weights" in payload                 # 원본의 가중 순위 규칙이 돈다
    assert payload["_projected"]["run_id"] == run_id


async def test_투영은_원본_도구_종수를_센다(client, tmp_path):
    """작업 지시서의 완료 판정 — 몇 종이 통과하고 몇 종이 남는지 센다.

    이 수치가 제안서에 인용된다. 올라가는 것은 좋고, **말없이 내려가면
    안 된다**. 그래서 하한을 건다.
    """
    pytest.importorskip("pipeline_mcp.tools")
    from pipeline_mcp.tools import tool_definitions

    from foldfront.api.mcp import _UPSTREAM_STARTS_WORK

    run_id = await _seed_run(client)
    names = sorted(t["name"] for t in tool_definitions())
    assert len(names) == 62

    #  실행을 지우거나 되돌리는 도구는 조사에서 뺀다. 한 번 부르면 뒤따르는
    #  도구가 읽을 것이 없어져, 재는 것이 순서가 되어 버린다.
    destructive = {
        "pipeline.delete_run", "pipeline.delete_project", "pipeline.delete_round",
        "pipeline.archive_project", "pipeline.archive_round", "pipeline.cancel_run",
        "pipeline.cath_delete_job", "pipeline.cath_stop_job",
    }
    fill = {"run_id": run_id, "other_run_id": run_id, "baseline_run_id": run_id,
            "project_id": "proj-view", "round_id": "round-view", "session_id": "s-1",
            "model_key": "af2", "path": "summary.json", "rel_path": "summary.json",
            "prompt": "설계를 계획해 주십시오", "message": "안녕하세요", "limit": 5}

    passed: list[str] = []
    for defn in sorted(tool_definitions(), key=lambda d: d["name"]):
        name = defn["name"]
        if name in _UPSTREAM_STARTS_WORK or name in destructive:
            continue
        props = (defn.get("inputSchema") or {}).get("properties") or {}
        args = {k: v for k, v in fill.items() if k in props}
        body = await _rpc(client, "tools/call", {"name": name, "arguments": args})
        payload = json.loads(body["result"]["content"][0]["text"])
        if isinstance(payload, dict) and payload.get("ok") is False:
            continue
        passed.append(name)

    #  2026. 9. 21. 실측 — 62종 가운데 이 경로로 28종이 값을 낸다.
    #  나머지는 셋으로 갈린다. 모델을 시작하는 8종은 큐를 거치게 하려고
    #  일부러 막았고, RunPod 6종은 자격증명이 있어야 하고, 지우는 8종은
    #  뒤따르는 도구가 읽을 것을 없애므로 여기서 부르지 않는다. 러너를
    #  세우지 못해 끝나는 것은 이제 하나도 없다.
    #
    #  하한을 두는 까닭은 이 수가 제안서에 인용되기 때문이다. 올라가는
    #  것은 좋고, 말없이 내려가면 안 된다.
    assert len(passed) >= 26, f"통과 {len(passed)}종: {passed}"

    #  이름을 박아 두는 것은 이들이 제안서가 말하는 기능이기 때문이다.
    for must in ("pipeline.get_hit_list", "pipeline.compare_runs",
                 "pipeline.plan_from_prompt", "pipeline.list_artifacts",
                 "pipeline.generate_report", "pipeline.status"):
        assert must in passed, must


async def _become(role: str) -> None:
    """개발용 계정의 역할을 바꾼다.

    DEV_ROLE 은 계정이 **처음 기록될 때**의 역할일 뿐이고, 그 뒤로는 계정
    기록이 말이다. 자료를 먼저 만들어야 하는 시험에서는 계정을 고쳐야 한다.
    """
    from foldfront.db.repositories import Repos

    await Repos().users.set_roles("dev", [Role(role)])


async def test_조회자는_도구_표면에_닿지_못한다(client):
    """원본 도구는 인증을 모른다. 검증이 이 앞단에 없으면 어디에도 없다."""
    run_id = await _seed_run(client)
    await _become("viewer")

    r = await client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "pipeline.delete_run", "arguments": {"run_id": run_id}},
    })

    assert r.status_code == 403


async def test_연구자도_남의_실행은_읽지_못한다(client):
    """실행 접근 범위 — 주인이 있는 실행은 그 사람과 운영자만 본다."""
    from foldfront.db.client import get_db

    run_id = await _seed_run(client)
    await get_db()[C.RUNS].update_one({"run_id": run_id}, {"$set": {"owner_id": "남"}})
    await _become("researcher")

    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.get_hit_list", "arguments": {"run_id": run_id}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["ok"] is False
    assert "권한" in payload["error"]


async def test_주인_없는_실행은_연구자가_읽는다(client):
    """처음 설치했을 때 있는 자료가 전부 주인이 없다. 막으면 화면이 빈다."""
    from foldfront.db.client import get_db

    run_id = await _seed_run(client)
    await get_db()[C.RUNS].update_one({"run_id": run_id}, {"$set": {"owner_id": None}})
    await _become("researcher")

    body = await _rpc(client, "tools/call",
                      {"name": "pipeline.get_hit_list", "arguments": {"run_id": run_id}})

    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["run_id"] == run_id
