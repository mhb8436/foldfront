"""HTTP API 시험.

실제 ASGI 응용에 붙어 돈다. 경로·상태코드·응답 형태를 고정한다.
"""

from __future__ import annotations

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
    """시험 전용 DB 로 갈아 끼운다.

    전역 Motor 클라이언트는 만들어진 이벤트 루프에 묶인다. pytest-asyncio 가 시험마다
    새 루프를 열므로 앞뒤로 비워 주지 않으면 「Event loop is closed」가 난다.
    """
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_api")

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


async def _seed_models(client: AsyncClient, names=("msa", "design", "af2")) -> None:
    for name in names:
        r = await client.post("/api/v1/models", json={
            "model_id": name, "version": "v1", "endpoint_id": f"ep-{name}",
            "active": True, "is_default": True,
        })
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------- 명세


async def test_openapi_명세를_낸다(client: AsyncClient):
    """OpenAPI 수준의 명세를 제공한다."""
    r = await client.get("/openapi.json")

    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1/runs" in spec["paths"]
    assert "/api/v1/models/{model_id}/resolve" in spec["paths"]


async def test_healthz(client: AsyncClient):
    r = await client.get("/healthz")

    assert r.status_code == 200
    assert r.json()["mongo_ok"] is True


# ---------------------------------------------------------------- 모델


async def test_모델을_등록하고_조회한다(client: AsyncClient):
    """모델 등록."""
    r = await client.post("/api/v1/models", json={
        "model_id": "rfd3", "version": "2026-09-01", "kind": "backbone",
        "endpoint_id": "ep-rfd3", "active": True, "is_default": True,
        "resources": {"gpu_count": 1, "gpu_memory_gb": 24.0},
    })
    assert r.status_code == 200

    listed = await client.get("/api/v1/models", params={"model_id": "rfd3"})
    assert listed.json()["count"] == 1
    assert listed.json()["items"][0]["container_image"] is None


async def test_동적_라우팅_경로(client: AsyncClient):
    """모델 해석."""
    await _seed_models(client, ["af2"])

    r = await client.get("/api/v1/models/af2/resolve")

    assert r.status_code == 200
    assert r.json() == {
        "model_id": "af2", "version": "v1", "transport": "runpod",
        "target": "ep-af2", "gpu_count": 0, "timeout_seconds": 21600.0,
    }


async def test_없는_모델은_404(client: AsyncClient):
    r = await client.get("/api/v1/models/없는모델/resolve")

    assert r.status_code == 404
    assert "찾지 못했" in r.json()["error"]["message"]


async def test_승인과_비활성_전환(client: AsyncClient):
    """모델 승인."""
    await client.post("/api/v1/models", json={
        "model_id": "custom", "version": "v1", "endpoint_id": "ep",
        "active": True, "is_default": True, "approval_status": "pending",
    })

    #  승인 전에는 라우팅되지 않는다
    assert (await client.get("/api/v1/models/custom/resolve")).status_code == 404

    ok = await client.post(
        "/api/v1/models/custom/v1/approve", params={"approved_by": "admin-1"}
    )
    assert ok.status_code == 200
    assert (await client.get("/api/v1/models/custom/resolve")).status_code == 200

    #  비활성으로 돌리면 다시 막힌다
    await client.post("/api/v1/models/custom/v1/active", params={"active": False})
    assert (await client.get("/api/v1/models/custom/resolve")).status_code == 404


# ---------------------------------------------------------------- 워크플로


async def test_기본_템플릿을_등록하고_목록에서_본다(client: AsyncClient):
    r = await client.post("/api/v1/workflows/builtin", json=["msa", "design", "af2"])

    assert r.status_code == 200
    assert r.json()["is_builtin"] is True

    listed = await client.get("/api/v1/workflows", params={"templates_only": True})
    assert listed.json()["count"] == 1


async def test_워크플로_저장은_그래프를_검증한다(client: AsyncClient):
    """실행할 때 알면 늦다."""
    bad = {
        "workflow_id": "wf-cycle", "name": "순환",
        "nodes": [{"node_id": "a"}, {"node_id": "b"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    }

    r = await client.post("/api/v1/workflows", json=bad)

    assert r.status_code == 400
    assert "순환" in r.json()["error"]["message"]


async def test_저장하면_층_구조와_미등록_모델을_알려준다(client: AsyncClient):
    await _seed_models(client, ["msa"])
    body = {
        "workflow_id": "wf-1", "name": "병렬",
        "nodes": [
            {"node_id": "a", "kind": "model", "model_id": "msa"},
            {"node_id": "b", "kind": "model", "model_id": "미등록"},
            {"node_id": "c", "kind": "model", "model_id": "msa"},
        ],
        "edges": [
            {"source": "a", "target": "b"},
            {"source": "a", "target": "c"},
        ],
    }

    r = await client.post("/api/v1/workflows", json=body)

    assert r.status_code == 200
    data = r.json()
    assert data["levels"] == [["a"], ["b", "c"]]
    #  등록되지 않은 모델은 경고만 한다 — 저장은 막지 않는다
    assert data["unregistered_models"] == ["미등록"]
    assert data["workflow"]["version"] == 1


async def test_저장할_때마다_버전이_쌓인다(client: AsyncClient):
    body = {"workflow_id": "wf-v", "name": "1차", "nodes": [{"node_id": "a"}], "edges": []}
    await client.post("/api/v1/workflows", json=body)
    await client.post("/api/v1/workflows", json={**body, "name": "2차"})

    versions = await client.get("/api/v1/workflows/wf-v/versions")
    assert versions.json()["versions"] == [1, 2]
    assert (await client.get("/api/v1/workflows/wf-v")).json()["name"] == "2차"
    assert (await client.get(
        "/api/v1/workflows/wf-v", params={"version": 1}
    )).json()["name"] == "1차"


async def test_preflight(client: AsyncClient):
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design", "af2"])

    r = await client.post("/api/v1/workflows/builtin-pipeline/preflight")

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["node_count"] == 3
    assert data["levels"] == [["msa"], ["design"], ["af2"]]


async def test_preflight_가_미등록_모델을_짚는다(client: AsyncClient):
    await _seed_models(client, ["msa"])
    await client.post("/api/v1/workflows/builtin", json=["msa", "design"])

    data = (await client.post("/api/v1/workflows/builtin-pipeline/preflight")).json()

    assert data["ok"] is False
    assert len(data["errors"]) == 1


# ---------------------------------------------------------------- 실행


async def test_실행_전체_흐름(client: AsyncClient):
    """시작 → 노드 완료 → 다음 노드 → 종료."""
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design", "af2"])

    started = await client.post("/api/v1/runs", json={
        "workflow_id": "builtin-pipeline",
        "request": {"target_fasta": "x.fasta"},
        "owner_id": "u-1",
    })
    assert started.status_code == 200
    run_id = started.json()["run_id"]
    assert started.json()["status"] == "running"

    #  첫 노드가 큐에 있다
    lease = await client.post("/api/v1/jobs/lease", json={"worker_id": "w1"})
    assert lease.json()["node_id"] == "msa"
    assert lease.json()["payload"]["route"]["target"] == "ep-msa"

    #  끝내면 다음 노드가 들어간다
    done = await client.post(
        f"/api/v1/runs/{run_id}/nodes/msa/complete",
        json={"succeeded": True, "result": {"depth": 120}},
    )
    assert done.json()["queued"] == ["design"]

    for node in ("design", "af2"):
        await client.post(
            f"/api/v1/runs/{run_id}/nodes/{node}/complete", json={"succeeded": True}
        )

    final = await client.get(f"/api/v1/runs/{run_id}")
    assert final.json()["status"] == "succeeded"


async def test_없는_워크플로로_시작하면_404(client: AsyncClient):
    r = await client.post("/api/v1/runs", json={"workflow_id": "없는것"})

    assert r.status_code == 404


async def test_실행_목록과_이벤트(client: AsyncClient):
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa"])
    run_id = (await client.post(
        "/api/v1/runs", json={"workflow_id": "builtin-pipeline"}
    )).json()["run_id"]

    listed = await client.get("/api/v1/runs")
    assert listed.json()["count"] == 1

    events = await client.get(f"/api/v1/runs/{run_id}/events")
    assert events.json()["count"] >= 1
    assert events.json()["items"][0]["message"] .startswith("실행을 시작했")


async def test_fork(client: AsyncClient):
    """fork 는 원본을 손대지 않는다."""
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design", "af2"])
    run_id = (await client.post(
        "/api/v1/runs", json={"workflow_id": "builtin-pipeline"}
    )).json()["run_id"]
    await client.post(f"/api/v1/runs/{run_id}/nodes/msa/complete", json={"succeeded": True})

    forked = await client.post(
        f"/api/v1/runs/{run_id}/fork", params={"from_stage": "design"}
    )

    assert forked.status_code == 200
    child = forked.json()
    assert child["forked_from_run_id"] == run_id
    assert [s["name"] for s in child["stages"]] == ["msa"]


async def test_취소(client: AsyncClient):
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design"])
    run_id = (await client.post(
        "/api/v1/runs", json={"workflow_id": "builtin-pipeline"}
    )).json()["run_id"]

    r = await client.post(f"/api/v1/runs/{run_id}/cancel", params={"reason": "중단"})

    assert r.json()["status"] == "cancelled"
    assert (await client.get("/api/v1/jobs/stats")).json()["by_status"].get("cancelled") == 1


# ---------------------------------------------------------------- 큐 · 감사


async def test_큐_통계와_회수(client: AsyncClient):
    """큐 현황과 만료 회수."""
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa"])
    await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})

    stats = await client.get("/api/v1/jobs/stats")
    assert stats.json()["by_status"]["queued"] == 1

    #  lease 를 만료 상태로 잡았다가 회수한다
    await client.post("/api/v1/jobs/lease", json={"worker_id": "w1", "lease_seconds": -1})
    reclaimed = await client.post("/api/v1/jobs/reclaim")
    assert reclaimed.json()["reclaimed"] == 1


async def test_감사로그_조회(client: AsyncClient):
    """감사 기록 조회."""
    await _seed_models(client, ["msa"])

    r = await client.get("/api/v1/audit", params={"action": "model.register"})

    assert r.json()["count"] == 1
    assert r.json()["items"][0]["target_id"] == "msa:v1"


async def test_프로젝트와_라운드(client: AsyncClient):
    """프로젝트·라운드."""
    p = await client.post("/api/v1/projects", json={"project_id": "p1", "name": "효소"})
    assert p.status_code == 200

    await client.post("/api/v1/rounds", json={"round_id": "r1", "project_id": "p1", "index": 1})

    rounds = await client.get("/api/v1/projects/p1/rounds")
    assert rounds.json()["count"] == 1


#  ---------------------------------------------------------------- 산출물 내용


async def _register_artifact(tmp_path, monkeypatch, client, *, rel: str, body: bytes = b"ATOM\n"):
    """저장 루트를 임시 디렉토리로 돌리고 산출물 1건을 등록한다."""
    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    from foldfront.core.config import get_settings

    get_settings.cache_clear()

    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)

    from foldfront.db.models import Artifact
    from foldfront.db.repositories import Repos

    await Repos().artifacts.register(
        Artifact(run_id="run-shot", stage="af2", path=rel, kind="pdb",
                 size_bytes=len(body), content_type="chemical/x-pdb")
    )
    return target


async def test_등록된_산출물의_내용을_내려준다(client, tmp_path, monkeypatch):
    await _register_artifact(tmp_path, monkeypatch, client,
                             rel="run-shot/af2/best_design.pdb", body=b"ATOM   1  N\n")

    r = await client.get("/api/v1/runs/run-shot/artifacts/content",
                         params={"path": "run-shot/af2/best_design.pdb"})

    assert r.status_code == 200
    assert r.content == b"ATOM   1  N\n"
    assert r.headers["content-type"].startswith("chemical/x-pdb")


async def test_등록되지_않은_경로는_내려주지_않는다(client, tmp_path, monkeypatch):
    """경로를 파일시스템에 그대로 넘기지 않는다. 저장소에 없으면 존재해도 거절한다."""
    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    from foldfront.core.config import get_settings

    get_settings.cache_clear()
    secret = tmp_path / "run-shot" / "af2" / "몰래.pdb"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"ATOM\n")

    r = await client.get("/api/v1/runs/run-shot/artifacts/content",
                         params={"path": "run-shot/af2/몰래.pdb"})

    assert r.status_code == 404


async def test_저장_루트를_벗어나는_등록은_거절한다(client, tmp_path, monkeypatch):
    """등록 자체가 오염된 경우까지 막는다 — 해석한 실제 경로를 루트와 다시 대조한다."""
    outside = tmp_path.parent / "루트밖.pdb"
    outside.write_bytes(b"ATOM\n")
    await _register_artifact(tmp_path, monkeypatch, client, rel="run-shot/af2/ok.pdb")

    from foldfront.db.models import Artifact
    from foldfront.db.repositories import Repos

    await Repos().artifacts.register(
        Artifact(run_id="run-shot", path="../루트밖.pdb", kind="pdb", stage="af2")
    )

    r = await client.get("/api/v1/runs/run-shot/artifacts/content",
                         params={"path": "../루트밖.pdb"})

    assert r.status_code == 400


async def test_실체_파일이_없으면_404_를_낸다(client, tmp_path, monkeypatch):
    await _register_artifact(tmp_path, monkeypatch, client, rel="run-shot/af2/ok.pdb")

    from foldfront.db.models import Artifact
    from foldfront.db.repositories import Repos

    await Repos().artifacts.register(
        Artifact(run_id="run-shot", path="run-shot/af2/사라짐.pdb", kind="pdb", stage="af2")
    )

    r = await client.get("/api/v1/runs/run-shot/artifacts/content",
                         params={"path": "run-shot/af2/사라짐.pdb"})

    assert r.status_code == 404
