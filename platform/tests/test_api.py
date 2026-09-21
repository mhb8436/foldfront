"""HTTP API 시험.

실제 ASGI 응용에 붙어 돈다. 경로·상태코드·응답 형태를 고정한다.
"""

from __future__ import annotations

import socket
from pathlib import Path

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


async def test_검토지점_경로가_실행을_멈추고_승인으로_풀린다(client: AsyncClient):
    """화면이 쓰는 경로 그대로 — 멈춤 · 승인 · 재개."""
    await _seed_models(client, ["msa", "design"])
    await client.post("/api/v1/workflows", json={
        "workflow_id": "wf-gate-api", "name": "검토",
        "nodes": [
            {"node_id": "msa", "kind": "model", "model_id": "msa"},
            {"node_id": "review", "kind": "checkpoint",
             "params": {"instructions": "정렬 깊이를 확인하십시오"}},
            {"node_id": "design", "kind": "model", "model_id": "design"},
        ],
        "edges": [
            {"source": "msa", "target": "review"},
            {"source": "review", "target": "design"},
        ],
    })
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "wf-gate-api"})).json()["run_id"]
    await client.post(f"/api/v1/runs/{run_id}/nodes/msa/complete", json={"succeeded": True})

    held = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert held["status"] == "paused"
    assert held["paused_at_node"] == "review"

    ok = await client.post(
        f"/api/v1/runs/{run_id}/nodes/review/review",
        params={"approved": True, "note": "확인했습니다"},
    )
    assert ok.status_code == 200 and ok.json()["queued"] == ["design"]
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["status"] == "running"


async def test_검토_대기중에는_그냥_재개할_수_없다(client: AsyncClient):
    await _seed_models(client, ["msa", "design"])
    await client.post("/api/v1/workflows", json={
        "workflow_id": "wf-gate-api2", "name": "검토2",
        "nodes": [
            {"node_id": "msa", "kind": "model", "model_id": "msa"},
            {"node_id": "review", "kind": "checkpoint"},
            {"node_id": "design", "kind": "model", "model_id": "design"},
        ],
        "edges": [
            {"source": "msa", "target": "review"},
            {"source": "review", "target": "design"},
        ],
    })
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "wf-gate-api2"})).json()["run_id"]
    await client.post(f"/api/v1/runs/{run_id}/nodes/msa/complete", json={"succeeded": True})

    r = await client.post(f"/api/v1/runs/{run_id}/resume")

    assert r.status_code == 409


async def test_중지와_재개_경로(client: AsyncClient):
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design"])
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})).json()["run_id"]

    #  워커가 첫 작업을 들고 있는 중에 중지한다 — 나간 작업은 건드리지 않는다
    assert (await client.post("/api/v1/jobs/lease", json={"worker_id": "w1"})).json()["node_id"] == "msa"
    paused = await client.post(f"/api/v1/runs/{run_id}/pause", params={"reason": "장비 점검"})
    assert paused.status_code == 200 and paused.json()["status"] == "paused"

    #  들고 있던 작업은 끝까지 가되, 다음 노드는 큐에 들어가지 않는다
    done = await client.post(f"/api/v1/runs/{run_id}/nodes/msa/complete", json={"succeeded": True})
    assert done.json()["queued"] == []
    assert (await client.post("/api/v1/jobs/lease", json={"worker_id": "w1"})).json() is None

    resumed = await client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "running"
    assert (await client.post("/api/v1/jobs/lease", json={"worker_id": "w1"})).json()["node_id"] == "design"


async def test_단계_재실행_경로(client: AsyncClient):
    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa", "design"])
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})).json()["run_id"]
    for node in ("msa", "design"):
        await client.post(f"/api/v1/runs/{run_id}/nodes/{node}/complete", json={"succeeded": True})
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["status"] == "succeeded"

    again = await client.post(f"/api/v1/runs/{run_id}/nodes/msa/rerun")

    assert again.status_code == 200
    assert again.json()["reset"] == ["design", "msa"]
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["status"] == "running"


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


#  ---------------------------------------------------------------- 입력 파일
#
#  A console has no other way to supply a sequence: a browser cannot know a
#  path on the server. That makes this the one endpoint that writes a file
#  from something the caller sent, so what it refuses matters as much as what
#  it accepts.


FASTA = b">lys\nMKALIVLGLVLLSVTVQG\n"


async def test_서열_파일을_올리면_실행이_읽을_경로를_낸다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("lys.fasta", FASTA, "text/plain")})

    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "fasta"
    assert body["name"] == "lys.fasta"
    assert body["size_bytes"] == len(FASTA)
    #  실행이 그대로 읽을 수 있어야 한다
    assert Path(body["path"]).read_bytes() == FASTA


async def test_올린_파일은_저장_위치_안에만_쓴다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", FASTA, "text/plain")})

    assert Path(r.json()["path"]).resolve().is_relative_to(tmp_path.resolve())


async def test_올린_파일의_이름으로_경로를_만들지_않는다(client, tmp_path, monkeypatch):
    """The name that arrives is a label, never a path component. Building the
    stored name here makes a traversal impossible rather than merely caught."""
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post(
        "/api/v1/inputs",
        files={"file": ("../../../../etc/passwd.fasta", FASTA, "text/plain")},
    )

    stored = Path(r.json()["path"])
    assert stored.resolve().is_relative_to(tmp_path.resolve())
    assert "passwd" not in stored.name
    assert ".." not in str(stored)
    #  원래 이름은 사람이 알아보라고 남기되, 경로에는 쓰지 않는다
    assert r.json()["name"] == "passwd.fasta"


async def test_받지_않는_형식은_거절한다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("run.sh", b"rm -rf /", "text/plain")})

    assert r.status_code == 415
    assert r.json()["error"]["code"] == "input.type_rejected"
    #  거절한 것은 쓰지 않는다
    assert not list(tmp_path.rglob("*.sh"))


async def test_확장자가_없으면_거절한다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("noname", FASTA, "text/plain")})

    assert r.status_code == 415


async def test_빈_파일은_거절한다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b"   \n", "text/plain")})

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "input.empty"


async def test_너무_큰_파일은_거절한다(client, tmp_path, monkeypatch):
    """A mistaken upload should not be able to fill the disk."""
    from foldfront.api import routes
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(routes, "MAX_INPUT_BYTES", 16)
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b"A" * 64, "text/plain")})

    assert r.status_code == 413
    assert not list(tmp_path.rglob("*.fasta"))


async def test_올린_것을_감사_기록에_남긴다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings
    from foldfront.db.repositories import Repos

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    await client.post("/api/v1/inputs", files={"file": ("lys.fasta", FASTA, "text/plain")})

    records = await Repos().audit.search(action="input.upload")

    assert len(records) == 1
    assert records[0].detail["name"] == "lys.fasta"
    assert records[0].detail["bytes"] == len(FASTA)


async def test_두_번_올리면_서로_덮어쓰지_않는다(client, tmp_path, monkeypatch):
    """Two people uploading the same filename must not collide."""
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    a = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b">a\nMK\n", "text/plain")})
    b = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b">b\nQW\n", "text/plain")})

    assert a.json()["path"] != b.json()["path"]
    assert Path(a.json()["path"]).read_bytes() == b">a\nMK\n"


async def test_조회자는_파일을_올리지_못한다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("DEV_ROLE", "viewer")
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", FASTA, "text/plain")})

    assert r.status_code == 403


#  ---------------------------------------------------------------- notices


async def test_알림은_승인_대기_모델을_짚는다(client):
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="esmfold", version="v1", endpoint_id="ep",
                     approval_status="pending")
    )

    items = (await client.get("/api/v1/notices")).json()["items"]

    approval = [i for i in items if i["kind"] == "model.approval"]
    assert len(approval) == 1
    assert approval[0]["severity"] == "action"
    assert approval[0]["detail"] == "esmfold:v1"
    assert approval[0]["href"] == "/models"


async def test_승인된_모델은_알리지_않는다(client):
    """A bell that rings for the normal case is one people stop reading."""
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="af2", version="v1", endpoint_id="ep",
                     approval_status="approved")
    )

    items = (await client.get("/api/v1/notices")).json()["items"]

    assert [i for i in items if i["kind"] == "model.approval"] == []


async def test_알림은_최근_실패한_실행을_짚는다(client):
    from datetime import timedelta

    from foldfront.db.models import Run, RunStatus, StageState, utcnow
    from foldfront.db.repositories import Repos

    await Repos().runs.create(Run(
        run_id="run-bad", workflow_id="wf", status=RunStatus.FAILED,
        finished_at=utcnow() - timedelta(minutes=5),
        stages=[StageState(name="af2", status=RunStatus.FAILED, error="GPU 없음")],
    ))

    items = (await client.get("/api/v1/notices")).json()["items"]

    failed = [i for i in items if i["kind"] == "run.failed"]
    assert len(failed) == 1
    assert "GPU 없음" in failed[0]["detail"]


async def test_오래된_실패는_알리지_않는다(client):
    """Past the window it is history, and history lives on the monitor."""
    from datetime import timedelta

    from foldfront.db.models import Run, RunStatus, utcnow
    from foldfront.db.repositories import Repos

    await Repos().runs.create(Run(
        run_id="run-old", workflow_id="wf", status=RunStatus.FAILED,
        finished_at=utcnow() - timedelta(days=5),
    ))

    items = (await client.get("/api/v1/notices")).json()["items"]

    assert [i for i in items if i["kind"] == "run.failed"] == []


async def test_알림은_멈춘_실행을_짚는다(client):
    from datetime import timedelta

    from foldfront.db.models import Run, RunStatus, utcnow
    from foldfront.db.repositories import Repos

    stale = utcnow() - timedelta(hours=2)
    await Repos().runs.create(Run(
        run_id="run-stuck", workflow_id="wf", status=RunStatus.RUNNING,
        started_at=stale, updated_at=stale,
    ))

    stuck = [i for i in (await client.get("/api/v1/notices")).json()["items"]
             if i["kind"] == "run.stalled"]

    assert len(stuck) == 1
    assert stuck[0]["severity"] == "warning"
    assert "분째 변화 없음" in stuck[0]["detail"]


async def test_방금_시작한_실행은_멈춘_것이_아니다(client):
    from foldfront.db.models import Run, RunStatus
    from foldfront.db.repositories import Repos

    await Repos().runs.create(Run(run_id="run-fresh", workflow_id="wf",
                                  status=RunStatus.RUNNING))

    items = (await client.get("/api/v1/notices")).json()["items"]

    assert [i for i in items if i["kind"] == "run.stalled"] == []


async def test_조회자에게는_처리할_수_없는_알림을_내지_않는다(client, monkeypatch):
    """A row that does nothing when clicked is worse than no row."""
    from foldfront.core.config import get_settings
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="esmfold", version="v1", endpoint_id="ep",
                     approval_status="pending")
    )
    monkeypatch.setenv("DEV_ROLE", "viewer")
    get_settings.cache_clear()

    kinds = {i["kind"] for i in (await client.get("/api/v1/notices")).json()["items"]}

    assert "model.approval" not in kinds
    assert "auth.disabled" not in kinds


async def test_운영자는_인증이_꺼진_것을_듣는다(client):
    """An installation serving real work without authentication should say so
    somewhere a person looks, not only in /healthz."""
    kinds = {i["kind"] for i in (await client.get("/api/v1/notices")).json()["items"]}

    assert "auth.disabled" in kinds


async def test_알림_식별자는_되풀이해도_같다(client):
    """The console remembers what has been read by id. An id that changed
    between polls would leave everything unread for ever."""
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="esmfold", version="v1", endpoint_id="ep",
                     approval_status="pending")
    )

    first = {i["id"] for i in (await client.get("/api/v1/notices")).json()["items"]}
    second = {i["id"] for i in (await client.get("/api/v1/notices")).json()["items"]}

    assert first == second


#  ---------------------------------------------------------------- dashboard


async def test_요약은_한_번에_모든_현황을_낸다(client):
    """The dashboard draws from this alone; six round trips would be worse."""
    r = await client.get("/api/v1/summary")

    assert r.status_code == 200
    body = r.json()
    assert {"runs", "jobs", "models", "workflows", "audit"} <= set(body)
    assert {"total", "by_status", "recent"} <= set(body["runs"])
    assert {"total", "active", "pending_approval"} <= set(body["models"])
    assert "gpu_in_use" in body["jobs"]


async def test_요약의_최근_실행_개수를_지정한다(client):
    from foldfront.db.models import Run
    from foldfront.db.repositories import Repos

    for i in range(4):
        await Repos().runs.create(Run(run_id=f"run-sum-{i}", workflow_id="wf"))

    r = await client.get("/api/v1/summary", params={"recent": 2})

    assert len(r.json()["runs"]["recent"]) == 2
    assert r.json()["runs"]["total"] == 4


async def test_요약은_승인_대기_모델을_가려낸다(client):
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="ok", version="v1", endpoint_id="ep", approval_status="approved")
    )
    await Repos().models.register(
        ModelVersion(model_id="waiting", version="v1", endpoint_id="ep", approval_status="pending")
    )

    body = (await client.get("/api/v1/summary")).json()

    assert body["models"]["total"] == 2
    assert [m["model_id"] for m in body["models"]["pending_approval"]] == ["waiting"]


async def test_요약을_프로젝트로_좁힌다(client):
    """A project sees its own runs and nobody else's."""
    from foldfront.db.models import Run
    from foldfront.db.repositories import Repos

    r = Repos()
    await r.runs.create(Run(run_id="run-mine", workflow_id="wf", project_id="proj-a"))
    await r.runs.create(Run(run_id="run-theirs", workflow_id="wf", project_id="proj-b"))
    await r.runs.create(Run(run_id="run-loose", workflow_id="wf"))

    scoped = (await client.get("/api/v1/summary", params={"project_id": "proj-a"})).json()

    assert scoped["runs"]["total"] == 1
    assert [x["run_id"] for x in scoped["runs"]["recent"]] == ["run-mine"]
    assert scoped["scope"]["project_id"] == "proj-a"


async def test_프로젝트_없이_요약하면_전부_낸다(client):
    """Including runs made before projects existed, which belong to none.

    Hiding those by default would make them unreachable from the console.
    """
    from foldfront.db.models import Run
    from foldfront.db.repositories import Repos

    r = Repos()
    await r.runs.create(Run(run_id="run-mine", workflow_id="wf", project_id="proj-a"))
    await r.runs.create(Run(run_id="run-loose", workflow_id="wf"))

    whole = (await client.get("/api/v1/summary")).json()

    assert whole["runs"]["total"] == 2
    assert whole["scope"]["project_id"] is None


async def test_요약의_큐와_모델은_프로젝트로_좁히지_않는다(client):
    """A queue is shared. Showing a project its own slice of it would suggest
    nothing else was waiting for the same GPUs."""
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="af2", version="v1", endpoint_id="ep")
    )

    scoped = (await client.get("/api/v1/summary", params={"project_id": "proj-a"})).json()

    assert scoped["models"]["total"] == 1


async def test_요약은_감사_기록을_함께_낸다(client):
    """The audit branch had no records under test and shipped broken once:
    search() returns documents, not dictionaries."""
    from foldfront.db.repositories import Repos

    await Repos().audit.record("run.create", actor_id="someone", target_id="run-1")

    entry = (await client.get("/api/v1/summary")).json()["audit"][0]

    assert entry["action"] == "run.create"
    assert entry["actor_id"] == "someone"
    assert entry["target_id"] == "run-1"
    assert entry["created_at"]


#  ---------------------------------------------------------------- 검증에서 드러난 것


async def test_길이를_밝히지_않은_업로드는_받지_않는다(client, tmp_path, monkeypatch):
    """Without Content-Length the whole body would be spooled to disk before
    the cap could refuse it. A browser always sends the length for a form."""
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    async def chunks():
        yield b"--zz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.fasta\"\r\n\r\n"
        yield b">a\nMK\n"
        yield b"\r\n--zz--\r\n"

    r = await client.post(
        "/api/v1/inputs", content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=zz"},
    )

    assert r.status_code == 411
    assert not list(tmp_path.rglob("*.fasta"))


async def test_밝힌_길이가_상한을_넘으면_읽기_전에_거절한다(client, tmp_path, monkeypatch):
    from foldfront.api import routes
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(routes, "MAX_INPUT_BYTES", 16)
    monkeypatch.setattr(routes, "MULTIPART_OVERHEAD", 0)
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b"A" * 64, "text/plain")})

    assert r.status_code == 413


async def test_붙여넣은_내용이_너무_크면_실행을_거절한다(client):
    """Pasted content had no cap at all, and the engine copied it into every
    job - eight copies of a 10MB paste for a seven-node workflow."""
    from foldfront.api import routes
    from foldfront.db.models import Workflow
    from foldfront.db.repositories import Repos

    await Repos().workflows.save(Workflow(workflow_id="wf-x", name="x"))
    monkeypatch_limit = 1024
    old = routes.MAX_INLINE_INPUT_BYTES
    routes.MAX_INLINE_INPUT_BYTES = monkeypatch_limit
    try:
        r = await client.post("/api/v1/runs", json={
            "workflow_id": "wf-x", "request": {"target_fasta": ">a\n" + "M" * 4096},
        })
    finally:
        routes.MAX_INLINE_INPUT_BYTES = old

    assert r.status_code == 413
    assert r.json()["error"]["code"] == "input.inline_too_large"


async def test_다른_프로젝트의_회차에는_실행을_기록하지_못한다(client):
    from foldfront.db.models import Project, Round, Workflow
    from foldfront.db.repositories import Repos

    r_ = Repos()
    await r_.workflows.save(Workflow(workflow_id="wf-x", name="x"))
    await r_.projects.create(Project(project_id="proj-a", name="A"))
    await r_.projects.create(Project(project_id="proj-b", name="B"))
    await r_.rounds.create(Round(round_id="round-a1", project_id="proj-a", index=1))

    r = await client.post("/api/v1/runs", json={
        "workflow_id": "wf-x", "project_id": "proj-b", "round_id": "round-a1", "request": {},
    })

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "round.not_in_project"


async def test_회차_번호는_서버가_매긴다(client):
    """Two browsers counting rounds at once would both have said 「3차」."""
    from foldfront.db.models import Project
    from foldfront.db.repositories import Repos

    await Repos().projects.create(Project(project_id="proj-a", name="A"))
    body = {"round_id": "", "project_id": "proj-a", "index": 99, "linked_run_ids": [], "archived": False}

    first = (await client.post("/api/v1/rounds", json=body)).json()
    second = (await client.post("/api/v1/rounds", json=body)).json()

    assert (first["index"], second["index"]) == (1, 2)


async def test_없는_프로젝트에는_회차를_열지_못한다(client):
    r = await client.post("/api/v1/rounds", json={
        "round_id": "", "project_id": "proj-없음", "index": 1, "linked_run_ids": [], "archived": False,
    })

    assert r.status_code == 404


async def test_거부된_모델은_승인_대기로_울리지_않는다(client):
    """A rejected model was decided. Ringing for it is a count that never
    reaches zero."""
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="bad", version="v1", endpoint_id="ep", approval_status="rejected")
    )

    notices = (await client.get("/api/v1/notices")).json()["items"]
    summary = (await client.get("/api/v1/summary")).json()

    assert [n for n in notices if n["kind"] == "model.approval"] == []
    assert summary["models"]["pending_approval"] == []


#  ---------------------------------------------------------------- 검증에서 드러난 것


async def test_길이를_밝히지_않은_업로드는_받지_않는다(client, tmp_path, monkeypatch):
    """Without Content-Length the whole body would be spooled to disk before
    the cap could refuse it. A browser always sends the length for a form."""
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    async def chunks():
        yield b"--zz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.fasta\"\r\n\r\n"
        yield b">a\nMK\n"
        yield b"\r\n--zz--\r\n"

    r = await client.post(
        "/api/v1/inputs", content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=zz"},
    )

    assert r.status_code == 411
    assert not list(tmp_path.rglob("*.fasta"))


async def test_밝힌_길이가_상한을_넘으면_읽기_전에_거절한다(client, tmp_path, monkeypatch):
    from foldfront.api import routes
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(routes, "MAX_INPUT_BYTES", 16)
    monkeypatch.setattr(routes, "MULTIPART_OVERHEAD", 0)
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("x.fasta", b"A" * 64, "text/plain")})

    assert r.status_code == 413


async def test_붙여넣은_내용이_너무_크면_실행을_거절한다(client, monkeypatch):
    """Pasted content had no cap at all, and the engine copied it into every
    job - eight copies of a 10MB paste for a seven-node workflow."""
    from foldfront.api import routes
    from foldfront.db.models import Workflow
    from foldfront.db.repositories import Repos

    await Repos().workflows.save(Workflow(workflow_id="wf-x", name="x"))
    monkeypatch.setattr(routes, "MAX_INLINE_INPUT_BYTES", 1024)

    r = await client.post("/api/v1/runs", json={
        "workflow_id": "wf-x", "request": {"target_fasta": ">a\n" + "M" * 4096},
    })

    assert r.status_code == 413
    assert r.json()["error"]["code"] == "input.inline_too_large"


async def test_다른_프로젝트의_회차에는_실행을_기록하지_못한다(client):
    from foldfront.db.models import Project, Round, Workflow
    from foldfront.db.repositories import Repos

    r_ = Repos()
    await r_.workflows.save(Workflow(workflow_id="wf-x", name="x"))
    await r_.projects.create(Project(project_id="proj-a", name="A"))
    await r_.projects.create(Project(project_id="proj-b", name="B"))
    await r_.rounds.create(Round(round_id="round-a1", project_id="proj-a", index=1))

    r = await client.post("/api/v1/runs", json={
        "workflow_id": "wf-x", "project_id": "proj-b", "round_id": "round-a1", "request": {},
    })

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "round.not_in_project"


async def test_회차_번호는_서버가_매긴다(client):
    """Two browsers counting rounds at once would both have said 「3차」."""
    from foldfront.db.models import Project
    from foldfront.db.repositories import Repos

    await Repos().projects.create(Project(project_id="proj-a", name="A"))
    body = {"round_id": "", "project_id": "proj-a", "index": 99, "linked_run_ids": [], "archived": False}

    first = (await client.post("/api/v1/rounds", json=body)).json()
    second = (await client.post("/api/v1/rounds", json=body)).json()

    assert (first["index"], second["index"]) == (1, 2)


async def test_없는_프로젝트에는_회차를_열지_못한다(client):
    r = await client.post("/api/v1/rounds", json={
        "round_id": "", "project_id": "proj-없음", "index": 1, "linked_run_ids": [], "archived": False,
    })

    assert r.status_code == 404


async def test_거부된_모델은_승인_대기로_울리지_않는다(client):
    """A rejected model was decided. Ringing for it is a count that never
    reaches zero."""
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    await Repos().models.register(
        ModelVersion(model_id="bad", version="v1", endpoint_id="ep", approval_status="rejected")
    )

    notices = (await client.get("/api/v1/notices")).json()["items"]
    summary = (await client.get("/api/v1/summary")).json()

    assert [n for n in notices if n["kind"] == "model.approval"] == []
    assert summary["models"]["pending_approval"] == []


#  ---------------------------------------------------------------- 입력 파일 보존·한도


async def test_올린_파일은_기록되고_사용량에_잡힌다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    await client.post("/api/v1/inputs", files={"file": ("a.fasta", FASTA, "text/plain")})

    usage = (await client.get("/api/v1/inputs/usage")).json()

    assert usage["used_bytes"] == len(FASTA)
    assert usage["quota_bytes"] > 0 and usage["retention_days"] > 0
    assert [i["name"] for i in usage["items"]] == ["a.fasta"]


async def test_한도를_넘기면_올리지_못한다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("INPUT_QUOTA_MB", "0")
    get_settings.cache_clear()

    r = await client.post("/api/v1/inputs", files={"file": ("a.fasta", FASTA, "text/plain")})

    assert r.status_code == 413
    assert r.json()["error"]["code"] == "input.quota_exceeded"
    assert not list(tmp_path.rglob("*.fasta"))


async def test_실행이_읽은_파일은_그_실행에_묶인다(client, tmp_path, monkeypatch):
    from foldfront.core.config import get_settings
    from foldfront.db.models import ModelVersion
    from foldfront.db.repositories import Repos

    from foldfront.engine.dag import builtin_pipeline_workflow

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    wf = await Repos().workflows.save(builtin_pipeline_workflow())
    await Repos().models.register(ModelVersion(model_id="msa", version="v1", endpoint_id="ep", active=True, is_default=True))
    up = (await client.post("/api/v1/inputs", files={"file": ("a.fasta", FASTA, "text/plain")})).json()

    started = await client.post("/api/v1/runs", json={
        "workflow_id": wf.workflow_id, "request": {"target_fasta": up["path"]},
    })
    assert started.status_code == 200, started.text
    run = started.json()

    items = await Repos().inputs.by_paths([up["path"]])
    assert items[0].run_ids == [run["run_id"]]


async def test_정리는_아무_실행도_읽지_않은_옛_파일만_지운다(client, tmp_path, monkeypatch):
    """A file a run read is that run's provenance and stays with it."""
    from datetime import timedelta

    from foldfront.core.config import get_settings
    from foldfront.db.models import utcnow
    from foldfront.db.repositories import Repos

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    old = (await client.post("/api/v1/inputs", files={"file": ("old.fasta", FASTA, "text/plain")})).json()
    kept = (await client.post("/api/v1/inputs", files={"file": ("kept.fasta", FASTA, "text/plain")})).json()
    fresh = (await client.post("/api/v1/inputs", files={"file": ("fresh.fasta", FASTA, "text/plain")})).json()
    long_ago = utcnow() - timedelta(days=90)
    await Repos().inputs.col.update_many(
        {"input_id": {"$in": [old["input_id"], kept["input_id"]]}}, {"$set": {"created_at": long_ago}},
    )
    await Repos().inputs.link_run([kept["path"]], "run-x")

    report = (await client.post("/api/v1/inputs/prune", params={"days": 30})).json()

    assert [i["name"] for i in report["items"]] == ["old.fasta"]
    assert not Path(old["path"]).exists()
    assert Path(kept["path"]).exists() and Path(fresh["path"]).exists()
    assert report["freed_bytes"] == len(FASTA)



async def test_실행이_읽은_파일도_한도에_든다(client, tmp_path, monkeypatch):
    """Review found the opposite rule made the allowance unbounded: naming a
    file in any run took it off the count for good. Everything counts; what
    is pinned is shown, not hidden."""
    from foldfront.core.config import get_settings
    from foldfront.db.repositories import Repos

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    up = (await client.post("/api/v1/inputs", files={"file": ("a.fasta", FASTA, "text/plain")})).json()
    assert (await client.get("/api/v1/inputs/usage")).json()["used_bytes"] == len(FASTA)

    await Repos().inputs.link_run([up["path"]], "run-x")

    usage = (await client.get("/api/v1/inputs/usage")).json()
    assert usage["used_bytes"] == len(FASTA)
    assert usage["items"][0]["run_ids"] == ["run-x"]



async def test_copilot_은_너무_긴_질문을_거절한다(client):
    r = await client.post("/api/v1/copilot/chat", json={
        "messages": [{"role": "user", "content": "가" * 5000}],
    })
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "copilot.too_long"


async def test_copilot_상태는_모델_이름만_말한다(client, monkeypatch):
    from foldfront.engine import copilot

    async def fake():
        return {"available": True, "model": "m", "url": "u", "served": ["m", "secret-other"]}

    monkeypatch.setattr(copilot, "available", fake)
    body = (await client.get("/api/v1/copilot/status")).json()
    assert "served" not in body and body["model"] == "m"


# ---------------------------------------------------------------- 보안: 조회


async def test_남의_실행은_조회도_막는다(client):
    """역할만 맞으면 누구나 남의 설계를 읽던 자리다."""
    from foldfront.db.client import get_db
    from foldfront.db.repositories import Repos
    from foldfront.db.models import Role

    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa"])
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})).json()["run_id"]
    await get_db()[C.RUNS].update_one({"run_id": run_id}, {"$set": {"owner_id": "남"}})
    await Repos().users.set_roles("dev", [Role.RESEARCHER])

    assert (await client.get(f"/api/v1/runs/{run_id}")).status_code == 403
    assert (await client.get(f"/api/v1/runs/{run_id}/events")).status_code == 403
    assert (await client.get(f"/api/v1/runs/{run_id}/artifacts")).status_code == 403
    r = await client.get(f"/api/v1/runs/{run_id}/artifacts/content", params={"path": "x.pdb"})
    assert r.status_code == 403


async def test_운영자는_남의_실행도_본다(client):
    from foldfront.db.client import get_db

    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa"])
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})).json()["run_id"]
    await get_db()[C.RUNS].update_one({"run_id": run_id}, {"$set": {"owner_id": "남"}})

    assert (await client.get(f"/api/v1/runs/{run_id}")).status_code == 200


async def test_산출물을_내려받으면_감사에_남는다(client, tmp_path, monkeypatch):
    """무엇이 설치 밖으로 나갔는지가 감사의 본령이다."""
    from foldfront.core.config import get_settings
    from foldfront.db.models import Artifact
    from foldfront.db.repositories import Repos

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    await _seed_models(client)
    await client.post("/api/v1/workflows/builtin", json=["msa"])
    run_id = (await client.post("/api/v1/runs", json={"workflow_id": "builtin-pipeline"})).json()["run_id"]

    rel = f"{run_id}/af2/best.pdb"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ATOM      1  N   MET A   1\n", encoding="utf-8")
    await Repos().artifacts.register(Artifact(
        run_id=run_id, stage="af2", path=rel, kind="pdb",
        size_bytes=target.stat().st_size,
    ))

    got = await client.get(f"/api/v1/runs/{run_id}/artifacts/content", params={"path": rel})
    assert got.status_code == 200

    logged = await Repos().audit.search(action="artifact.read")
    assert logged, "산출물 조회가 감사에 남지 않는다"
    assert logged[0].detail["path"] == rel
    assert logged[0].detail["bytes"] > 0


# ---------------------------------------------------------------- 보안: 세션


async def test_인증이_꺼져_있으면_로그아웃할_것이_없다고_말한다(client):
    """끝낼 세션이 없는데 끝난 척하지 않는다."""
    r = await client.post("/api/v1/auth/logout")

    assert r.status_code == 500 or r.json()["error"]["code"] == "auth.not_configured"


async def test_로그아웃하면_그_전에_발급된_토큰을_거절한다(client):
    """OIDC 토큰은 상태가 없다. 서명한 쪽은 여전히 유효하다고 한다."""
    from datetime import datetime, timedelta, timezone

    from foldfront.core.auth import Identity, apply_local
    from foldfront.core.errors import ApiError
    from foldfront.db.models import Role
    from foldfront.db.repositories import Repos

    now = datetime.now(timezone.utc)
    who = Identity(user_id="사람", subject="sub-1", email="", roles=(Role.RESEARCHER,),
                   authenticated=True, issued_at=now - timedelta(minutes=5))

    #  로그아웃 전에는 통한다
    assert (await apply_local(who)).user_id == "사람"

    await Repos().users.sign_out("사람")

    with pytest.raises(ApiError):
        await apply_local(who)

    #  다시 로그인해 받은 새 토큰은 통한다
    fresh = Identity(user_id="사람", subject="sub-1", email="", roles=(Role.RESEARCHER,),
                     authenticated=True, issued_at=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert (await apply_local(fresh)).user_id == "사람"


async def test_새_토큰이_오면_로그인을_한_번_기록한다(client):
    """seen 은 매 요청마다 돈다. 새 토큰만이 로그인이라 부를 수 있는 순간이다."""
    from datetime import datetime, timedelta, timezone

    from foldfront.core.auth import Identity, apply_local
    from foldfront.db.models import Role
    from foldfront.db.repositories import Repos

    issued = datetime.now(timezone.utc)
    who = Identity(user_id="사람2", subject="sub-2", email="", roles=(Role.RESEARCHER,),
                   authenticated=True, issued_at=issued)

    await apply_local(who)
    await apply_local(who)          # 같은 토큰으로 두 번째 요청
    await apply_local(who)

    logged = await Repos().audit.search(action="auth.login")
    mine = [a for a in logged if a.actor_id == "사람2"]
    assert len(mine) == 1, f"같은 토큰인데 {len(mine)}번 기록했다"

    #  새 토큰은 새 로그인이다
    await apply_local(Identity(user_id="사람2", subject="sub-2", email="",
                               roles=(Role.RESEARCHER,), authenticated=True,
                               issued_at=issued + timedelta(minutes=10)))
    again = [a for a in await Repos().audit.search(action="auth.login") if a.actor_id == "사람2"]
    assert len(again) == 2
