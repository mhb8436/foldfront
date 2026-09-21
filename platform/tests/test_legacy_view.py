"""투영기 시험 — MongoDB 의 실행을 원본이 읽는 디렉터리로 낸다.

원본 도구 62종은 전부 `<output_root>/<run_id>/request.json` 을 먼저 읽는다.
신규 계층은 실행을 MongoDB 에 두므로 그 파일이 없고, 그래서 도구가 전부
「run_id not found」로 끝났다. 여기서 보는 것은 그 사이가 이어졌는지다.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

from foldfront.db.client import C, ensure_indexes
from foldfront.db.models import (
    ModelVersion,
    NodeKind,
    ResourceSpec,
    RunStatus,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from foldfront.db.repositories import Repos
from foldfront.engine.legacy_view import LegacyProjector, ProjectionError
from foldfront.engine.service import ExecutionService


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def repos():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest_view"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


@pytest.fixture
async def finished(repos: Repos, tmp_path: Path):
    """끝까지 돈 실행 하나와, 그것을 낼 투영기."""
    for name in ("msa", "design", "af2"):
        await repos.models.register(ModelVersion(
            model_id=name, version="v1", endpoint_id=f"ep-{name}",
            active=True, is_default=True, resources=ResourceSpec(gpu_count=0),
        ))
    wf = await repos.workflows.save(Workflow(
        workflow_id="wf-view", name="투영",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="design", kind=NodeKind.MODEL, model_id="design"),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="design"),
            WorkflowEdge(source="design", target="af2"),
        ],
    ))
    svc = ExecutionService(repos)
    run = await svc.start(wf, request={"target_fasta": "x.fasta", "relax_enabled": True},
                          owner_id="u-1")
    await svc.complete_node(run.run_id, "msa", succeeded=True, result={"depth": 120})
    await svc.complete_node(run.run_id, "design", succeeded=True, result={"sequences": 40})
    await svc.complete_node(run.run_id, "af2", succeeded=True, result={"plddt": 88.1})
    return run.run_id, LegacyProjector(repos, output_root=tmp_path)


async def test_원본이_읽는_세_파일을_낸다(finished):
    run_id, projector = finished

    view = await projector.project(run_id)

    assert view.root.name == run_id
    for name in ("request.json", "status.json", "summary.json"):
        assert (view.root / name).is_file(), name
    assert "events.jsonl" in view.files


async def test_request_는_들어온_그대로_낸다(finished):
    """원본 PipelineRequest 는 필드 127개다. 이름을 바꾸지 않는다."""
    run_id, projector = finished
    view = await projector.project(run_id)

    request = json.loads((view.root / "request.json").read_text(encoding="utf-8"))

    assert request["target_fasta"] == "x.fasta"
    assert request["relax_enabled"] is True
    assert request["run_id"] == run_id


async def test_status_는_단계와_지표를_싣는다(finished):
    run_id, projector = finished
    view = await projector.project(run_id)

    status = json.loads((view.root / "status.json").read_text(encoding="utf-8"))

    assert status["status"] == "succeeded"
    stages = {s["name"]: s for s in status["stages"]}
    assert stages["af2"]["metrics"]["plddt"] == 88.1
    assert stages["msa"]["status"] == "succeeded"


async def test_멈춘_실행은_원본에_running_으로_보인다(repos: Repos, tmp_path: Path):
    """PAUSED 는 우리 것이다. 원본 어휘에 없으므로 「끝나지 않았다」로 낸다."""
    for name in ("msa", "design"):
        await repos.models.register(ModelVersion(
            model_id=name, version="v1", endpoint_id=f"ep-{name}",
            active=True, is_default=True, resources=ResourceSpec(gpu_count=0),
        ))
    wf = await repos.workflows.save(Workflow(
        workflow_id="wf-held", name="멈춤",
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa"),
            WorkflowNode(node_id="review", kind=NodeKind.CHECKPOINT),
            WorkflowNode(node_id="design", kind=NodeKind.MODEL, model_id="design"),
        ],
        edges=[
            WorkflowEdge(source="msa", target="review"),
            WorkflowEdge(source="review", target="design"),
        ],
    ))
    svc = ExecutionService(repos)
    run = await svc.start(wf)
    await svc.complete_node(run.run_id, "msa", succeeded=True)
    assert (await repos.runs.get(run.run_id)).status is RunStatus.PAUSED

    view = await LegacyProjector(repos, output_root=tmp_path).project(run.run_id)

    status = json.loads((view.root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "running"


async def test_두_번_내도_사건이_늘지_않는다(finished):
    """이어 쓰면 두 번째 투영에서 앞선 줄이 전부 겹친다."""
    run_id, projector = finished

    first = await projector.project(run_id)
    second = await projector.project(run_id)

    lines = (second.root / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert first.events == second.events == len(lines)


async def test_모델이_쓴_summary_는_덮어쓰지_않는다(finished):
    """tiers 는 실제로 돈 모델이 낸다. 지어내면 hit list 가 없는 파일을 찾는다."""
    run_id, projector = finished
    view = await projector.project(run_id)
    real = json.loads((view.root / "summary.json").read_text(encoding="utf-8"))
    real["tiers"] = [{"tier": 0.5, "proteinmpnn_samples": []}]
    (view.root / "summary.json").write_text(json.dumps(real), encoding="utf-8")

    again = await projector.project(run_id)

    kept = json.loads((again.root / "summary.json").read_text(encoding="utf-8"))
    assert kept["tiers"] == [{"tier": 0.5, "proteinmpnn_samples": []}]
    assert kept["status"] == "succeeded"          # 실행 정보는 갱신한다


async def test_tiers_를_지어내지_않는다(finished):
    """모델이 내지 않았으면 키가 없어야 한다. 있으면 없는 파일을 찾게 된다."""
    run_id, projector = finished

    view = await projector.project(run_id)

    summary = json.loads((view.root / "summary.json").read_text(encoding="utf-8"))
    assert "tiers" not in summary


async def test_없는_실행은_거절한다(repos: Repos, tmp_path: Path):
    projector = LegacyProjector(repos, output_root=tmp_path)

    with pytest.raises(ProjectionError):
        await projector.project("run-없는것")

    assert await projector.project_if_present("run-없는것") is None


@pytest.mark.parametrize("bad", ["../밖", "a/b", "..", "", "   ", "\\x"])
async def test_저장_루트를_벗어나는_식별자를_거절한다(repos: Repos, tmp_path: Path, bad: str):
    """실행 식별자는 도구 인자로 들어온다. 공격자가 쓰는 값으로 본다."""
    projector = LegacyProjector(repos, output_root=tmp_path)

    with pytest.raises(ProjectionError):
        projector.run_root(bad)


# ---------------------------------------------------------------- 원본 도구 왕복


def _upstream_available() -> bool:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline-mcp" / "src"))
    try:
        import pipeline_mcp.tools  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _upstream_available(), reason="원본 미포함 구성")
async def test_원본_도구가_투영된_실행을_읽는다(finished):
    """이 시험이 1번 항목의 완료 판정이다.

    투영 전에는 원본 도구가 「run_id not found」로 끝났다. 투영 뒤에 같은
    도구가 같은 실행을 읽어 값을 내야 한다.
    """
    from pipeline_mcp.pipeline import PipelineRunner
    from pipeline_mcp.tools import ToolDispatcher

    run_id, projector = finished
    root = str(projector.root)
    dispatcher = ToolDispatcher(runner=PipelineRunner(output_root=root))

    #  투영 전 — 실행 디렉터리가 없어 원본이 찾지 못한다
    with pytest.raises(ValueError, match="run_id not found"):
        dispatcher.call_tool("pipeline.get_hit_list", {"run_id": run_id})

    await projector.project(run_id)

    assert dispatcher.call_tool("pipeline.list_runs", {})["runs"] == [run_id]
    listed = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id})
    assert listed["run_id"] == run_id
    hits = dispatcher.call_tool("pipeline.get_hit_list", {"run_id": run_id})
    assert hits["run_id"] == run_id
    #  모델이 tiers 를 내지 않았으므로 후보는 0건이다. 이것이 정직한 답이다.
    assert hits["total_rows"] == 0
