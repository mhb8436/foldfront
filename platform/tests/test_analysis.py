"""분석 경로 시험 — 원본의 계산을 화면까지 나르는 길.

가중 순위·비교 지표는 원본의 것이다. 여기서 보는 것은 값이 아니라 **길**이다.
투영이 일어나는가, 접근 범위가 걸리는가, 없는 것을 없다고 말하는가.
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
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_analysis")
    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))

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


async def _run(client, *, metrics: dict | None = None) -> str:
    await client.post("/api/v1/models", json={
        "model_id": "soluprot", "version": "v1", "kind": "solubility",
        "endpoint_id": "ep-soluprot", "active": True, "is_default": True,
        "resources": {"gpu_count": 0},
    })
    await client.post("/api/v1/workflows", json={
        "workflow_id": "wf-a", "name": "분석",
        "nodes": [{"node_id": "soluprot", "kind": "model", "model_id": "soluprot"}],
        "edges": [],
    })
    started = await client.post("/api/v1/runs", json={
        "workflow_id": "wf-a", "request": {"target_fasta": "x.fasta"},
    })
    run_id = started.json()["run_id"]
    await client.post(f"/api/v1/runs/{run_id}/nodes/soluprot/complete", json={
        "succeeded": True,
        "result": metrics if metrics is not None else {"scored": 40, "passed": 4, "pass_rate": 0.1},
    })
    return run_id


# ---------------------------------------------------------------- hit list


async def test_후보군_순위가_원본_가중치_규칙으로_나온다(client):
    pytest.importorskip("pipeline_mcp.tools")
    run_id = await _run(client)

    r = await client.get(f"/api/v1/runs/{run_id}/hit-list")

    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == run_id
    #  가중치는 부르는 쪽이 정한다. 원본이 정규화해 돌려준다.
    assert set(body["weights"]) >= {"soluprot", "plddt", "rmsd"}
    assert "rows" in body and "stats" in body


async def test_가중치를_바꾸면_그대로_실린다(client):
    pytest.importorskip("pipeline_mcp.tools")
    run_id = await _run(client)

    r = await client.get(f"/api/v1/runs/{run_id}/hit-list",
                         params={"soluprot": 1.0, "plddt": 0.0, "rmsd": 0.0, "novelty": 0.0})

    assert r.json()["weights"]["soluprot"] == 1.0


async def test_후보가_없으면_없다고_말한다(client):
    """빈 표만 내면 후보가 나쁜 것인지 없는 것인지 읽는 사람이 모른다."""
    pytest.importorskip("pipeline_mcp.tools")
    run_id = await _run(client)

    body = (await client.get(f"/api/v1/runs/{run_id}/hit-list")).json()

    assert body["rows"] == []
    assert "tier" in body["empty_reason"]


async def test_없는_실행은_404(client):
    r = await client.get("/api/v1/runs/run-없는것/hit-list")
    assert r.status_code == 404


async def test_후보군_조회가_실행을_투영한다(client, tmp_path):
    """원본은 디렉터리를 읽는다. 부르기만 하면 투영이 따라와야 한다."""
    pytest.importorskip("pipeline_mcp.tools")
    run_id = await _run(client)
    assert not (tmp_path / run_id / "request.json").exists()

    await client.get(f"/api/v1/runs/{run_id}/hit-list")

    assert (tmp_path / run_id / "request.json").is_file()
    assert (tmp_path / run_id / "summary.json").is_file()


# ---------------------------------------------------------------- compare


async def test_두_실행을_비교한다(client):
    pytest.importorskip("pipeline_mcp.tools")
    a = await _run(client)
    b = await _run(client)

    r = await client.get(f"/api/v1/runs/{a}/compare", params={"baseline_run_id": b})

    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == a
    assert body["baseline_run_id"] == b


async def test_기준_실행이_없으면_404(client):
    pytest.importorskip("pipeline_mcp.tools")
    a = await _run(client)

    r = await client.get(f"/api/v1/runs/{a}/compare",
                         params={"baseline_run_id": "run-없는것"})

    assert r.status_code == 404


async def test_기준_실행에도_접근_범위가_걸린다(client):
    """기준만 검사하지 않으면 남의 실행을 읽는 뒷문이 된다."""
    pytest.importorskip("pipeline_mcp.tools")
    from foldfront.db.repositories import Repos

    a = await _run(client)
    b = await _run(client)
    await get_db()[C.RUNS].update_one({"run_id": b}, {"$set": {"owner_id": "남"}})
    await Repos().users.set_roles("dev", [Role.RESEARCHER])

    r = await client.get(f"/api/v1/runs/{a}/compare", params={"baseline_run_id": b})

    assert r.status_code == 403


# ---------------------------------------------------------------- quality


async def test_품질_신호가_단계를_읽는다(client):
    run_id = await _run(client)

    body = (await client.get(f"/api/v1/runs/{run_id}/quality")).json()

    assert body["run_id"] == run_id
    assert body["counts"]["warning"] == 1
    signal = body["signals"][0]
    assert signal["stage"] == "soluprot"
    assert signal["source"] == "agent_panel._interpret_soluprot"
    assert signal["advice"]


async def test_품질_신호는_모의_값을_판정하지_않는다(client):
    run_id = await _run(client, metrics={"scored": 40, "passed": 0, "_mock": True})

    body = (await client.get(f"/api/v1/runs/{run_id}/quality")).json()

    assert body["counts"]["error"] == 0
    assert "모의" in body["signals"][0]["message"]


async def test_투영_경로는_무엇을_썼는지_알려준다(client):
    run_id = await _run(client)

    r = await client.post(f"/api/v1/runs/{run_id}/project")

    assert r.status_code == 200
    assert "request.json" in r.json()["files"]
    assert "summary.json" in r.json()["files"]
