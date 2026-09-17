"""The design copilot.

The model is not under test - it is a local weight file and it says what it
says. What is under test is what it is handed: that the facts are this
installation's, bounded, scalar, and that the answer comes back with the
list of what was consulted. The completion is a fake here.
"""

from __future__ import annotations

import pytest

from foldfront.db.models import ModelVersion, Project, ResourceSpec, Round, RunStatus
from foldfront.engine import copilot
from foldfront.engine.dag import builtin_pipeline_workflow
from foldfront.engine.service import ExecutionService

pytestmark = pytest.mark.usefixtures("repos")


@pytest.fixture
async def repos():
    import socket

    from motor.motor_asyncio import AsyncIOMotorClient

    from foldfront.db.client import C, ensure_indexes
    from foldfront.db.repositories import Repos

    with socket.socket() as s:
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", 27017)) != 0:
            pytest.skip("MongoDB 미기동")
    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest_copilot"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


async def seed(repos):
    for name in ("msa", "rfd3", "bioemu", "design", "soluprot", "af2", "novelty"):
        await repos.models.register(ModelVersion(model_id=name, version="v1", endpoint_id=f"ep-{name}",
                                                 active=True, is_default=True, resources=ResourceSpec()))
    await repos.projects.create(Project(project_id="proj-a", name="리소자임"))
    await repos.rounds.create(Round(round_id="r1", project_id="proj-a", index=1, objective="기준선"))
    wf = await repos.workflows.save(builtin_pipeline_workflow())
    svc = ExecutionService(repos)
    run = await svc.start(wf, project_id="proj-a", round_id="r1")
    await svc.complete_node(run.run_id, "msa", succeeded=True,
                            result={"depth": 120, "results": [{"id": "a"}, {"id": "b"}], "_mock": True})
    return run


async def test_현황은_이_설치의_사실로_이뤄진다(repos):
    run = await seed(repos)

    ctx = await copilot.gather(repos, project_id="proj-a", run_id=run.run_id)
    text = copilot.render(ctx)

    assert "리소자임" in text and "1차" in text and "기준선" in text
    assert run.run_id in text and "msa [succeeded]" in text and "depth=120" in text
    assert "results=2개" in text          # a list becomes a count
    assert "_mock" not in text            # private metrics stay private
    assert ctx["used"] == ["프로젝트 리소자임 · 회차 1개", "최근 실행 1건", f"실행 {run.run_id} 의 단계·지표·사건 2건", "워크플로 1종"]


async def test_현황은_길이가_묶여_있다(repos):
    long = {"runs": [{"run_id": f"run-{i}", "status": "succeeded", "workflow_id": "wf", "round_id": None,
                      "started_at": None, "stages": ["x:succeeded"] * 40} for i in range(400)], "workflows": []}
    assert len(copilot.render(long)) <= copilot.MAX_CHARS + 40


async def test_답은_현황을_시스템_메시지로_받은_모델에서_온다(repos):
    run = await seed(repos)
    seen: list[list[dict]] = []

    async def fake(messages):
        seen.append(messages)
        return "<think>생각</think>msa 는 성공했고 depth 는 120 입니다."

    out = await copilot.chat(repos, [{"role": "user", "content": "msa 어떻게 됐어?"}],
                             project_id="proj-a", run_id=run.run_id, complete=fake)

    assert out["reply"] == "<think>생각</think>msa 는 성공했고 depth 는 120 입니다."  # stripping is the real completer's job
    assert seen[0][0]["role"] == "system" and run.run_id in seen[0][0]["content"]
    assert "실행을 시작하거나" in seen[0][0]["content"]
    assert seen[0][-1] == {"role": "user", "content": "msa 어떻게 됐어?"}
    assert out["context_used"][0].startswith("프로젝트")


async def test_모델이_없으면_없다고_한다(monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("LOCAL_LLM_URL", "http://127.0.0.1:9")  # nothing listens
    get_settings.cache_clear()
    try:
        status = await copilot.available()
    finally:
        get_settings.cache_clear()

    assert status["available"] is False
    assert status["model"]


async def test_실행이_없으면_없다고_적는다(repos):
    """Found on the first real question: an empty heading was read as an
    omission and the model described workflow definitions as run history."""
    await repos.projects.create(Project(project_id="proj-e", name="빈 프로젝트"))

    text = copilot.render(await copilot.gather(repos, project_id="proj-e", run_id=None))

    assert "없음 (이 범위에는 아직 실행이 없다)" in text
    assert "회차: 없음" in text
    assert "실행한 이력이 아님" in text
