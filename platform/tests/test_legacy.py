"""레거시 마이그레이션 시험.

현행 RAPID 의 출력 디렉토리 구조를 그대로 흉내 낸 표본을 만들어 옮긴다.
현행 status.json 은 단계 표현이 판마다 다르므로(사전/목록) 둘 다 시험한다.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from foldfront.db.client import C, ensure_indexes
from foldfront.db.legacy import LegacyMigrator, looks_like_run_dir
from foldfront.db.models import RunStatus
from foldfront.db.repositories import Repos


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")


@pytest.fixture
async def repos():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", tz_aware=True)
    db = client["foldfront_pytest_legacy"]
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    yield Repos(db)
    client.close()


@pytest.fixture
def legacy_root(tmp_path: Path) -> Path:
    """현행 출력 디렉토리를 흉내 낸다."""
    root = tmp_path / "runs"

    #  run 1 — status.json 이 단계를 사전으로 표현하는 판
    r1 = root / "run-20260901-abc"
    (r1 / "af2").mkdir(parents=True)
    (r1 / "msa").mkdir(parents=True)
    (r1 / "request.json").write_text(json.dumps({
        "target_fasta": "/data/targets/lysozyme.fasta",
        "target_pdb": "/data/targets/lysozyme.pdb",
        "design_chains": ["A"],
        "conservation_tiers": [0.3, 0.5, 0.7],
        "rfd3_use": True,
        "project_id": "proj-1",
        "round_id": "round-1",
    }), encoding="utf-8")
    (r1 / "status.json").write_text(json.dumps({
        "status": "succeeded",
        "started_at": "2026-09-01T01:00:00Z",
        "finished_at": "2026-09-01T03:20:00Z",
        "stages": {
            "msa": {"status": "succeeded", "model_id": "mmseqs", "model_version": "v2"},
            "design": {"status": "succeeded", "model_id": "proteinmpnn"},
            "af2": {"status": "failed", "error": "GPU 없음"},
        },
    }), encoding="utf-8")
    (r1 / "summary.json").write_text(json.dumps({"tiers": 3}), encoding="utf-8")
    (r1 / "events.jsonl").write_text(
        '{"ts": "2026-09-01T01:00:00Z", "stage": "msa", "message": "MSA 시작"}\n'
        '{"ts": "2026-09-01T01:30:00Z", "stage": "msa", "level": "info", "message": "MSA 완료"}\n'
        '\n'
        '깨진 줄 — 건너뛴다\n'
        '{"stage": "af2", "level": "error", "message": "GPU 없음"}\n',
        encoding="utf-8",
    )
    (r1 / "feedback.jsonl").write_text(
        '{"sequence_id": "seq-1", "verdict": "good", "score": 0.9, "comment": "발현 양호"}\n'
        '{"sequence_id": "seq-2", "rating": "bad", "score": 0.1}\n',
        encoding="utf-8",
    )
    (r1 / "experiments.jsonl").write_text(
        '{"sequence_id": "seq-1", "metric": "tm", "value": 72.5, "unit": "C"}\n',
        encoding="utf-8",
    )
    (r1 / "af2" / "ranked_0.pdb").write_text("ATOM      1  N\n", encoding="utf-8")
    (r1 / "af2" / "scores.json").write_text('{"plddt": 88.1}', encoding="utf-8")
    (r1 / "msa" / "aln.a3m").write_text(">q\nMKV\n", encoding="utf-8")

    #  run 2 — status.json 이 단계를 목록으로 표현하는 판
    r2 = root / "run-20260902-def"
    r2.mkdir(parents=True)
    (r2 / "request.json").write_text(json.dumps({"target_fasta": "x.fasta"}), encoding="utf-8")
    (r2 / "status.json").write_text(json.dumps({
        "status": "running",
        "stages": [{"name": "msa", "status": "running"}],
    }), encoding="utf-8")

    #  run 이 아닌 디렉토리 — 건너뛰어야 한다
    (root / "not-a-run").mkdir(parents=True)
    (root / "not-a-run" / "readme.txt").write_text("무시", encoding="utf-8")

    #  프로젝트·라운드
    pdir = root / "workspace" / "projects" / "proj-1"
    (pdir / "rounds").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "project_id": "proj-1", "name": "라이소자임 안정화", "tags": ["효소"],
    }), encoding="utf-8")
    (pdir / "rounds" / "round-1.json").write_text(json.dumps({
        "round_id": "round-1", "index": 1,
        "linked_run_ids": ["run-20260901-abc"],
    }), encoding="utf-8")

    return root


async def test_run_디렉토리를_알아본다(legacy_root: Path):
    assert looks_like_run_dir(legacy_root / "run-20260901-abc")
    assert not looks_like_run_dir(legacy_root / "not-a-run")


async def test_전체를_옮긴다(repos: Repos, legacy_root: Path):
    report = await LegacyMigrator(repos).migrate_root(legacy_root)

    assert report.runs == 2
    assert report.projects == 1
    assert report.rounds == 1
    assert report.errors == []
    #  깨진 줄 하나는 건너뛰고 3건만 들어간다
    assert report.events == 3
    assert report.feedback == 2
    assert report.experiments == 1
    #  관리 파일(request/status/summary/events/feedback/experiments)은 아티팩트가 아니다
    assert report.artifacts == 3


async def test_request_를_통째로_보존한다(repos: Repos, legacy_root: Path):
    """의 전제 — 필드 이름을 바꾸지 않는다."""
    await LegacyMigrator(repos).migrate_root(legacy_root)

    run = await repos.runs.get("run-20260901-abc")
    assert run.request["target_fasta"] == "/data/targets/lysozyme.fasta"
    assert run.request["rfd3_use"] is True
    #  조회용으로 승격한 값도 함께 채운다
    assert run.target_fasta_name == "lysozyme.fasta"
    assert run.design_chains == ["A"]
    assert run.conservation_tiers == [0.3, 0.5, 0.7]
    assert run.project_id == "proj-1"


async def test_단계_표현이_사전이든_목록이든_읽는다(repos: Repos, legacy_root: Path):
    await LegacyMigrator(repos).migrate_root(legacy_root)

    r1 = await repos.runs.get("run-20260901-abc")
    assert {s.name for s in r1.stages} == {"msa", "design", "af2"}
    msa = next(s for s in r1.stages if s.name == "msa")
    assert msa.status is RunStatus.SUCCEEDED
    assert msa.model_version == "v2"
    af2 = next(s for s in r1.stages if s.name == "af2")
    assert af2.status is RunStatus.FAILED
    assert af2.error == "GPU 없음"

    r2 = await repos.runs.get("run-20260902-def")
    assert [s.name for s in r2.stages] == ["msa"]
    assert r2.status is RunStatus.RUNNING


async def test_아티팩트에_단계와_종류를_붙인다(repos: Repos, legacy_root: Path):
    """최상위 디렉토리 이름으로 단계를 추정한다."""
    await LegacyMigrator(repos).migrate_root(legacy_root)

    arts = await repos.artifacts.list("run-20260901-abc")
    by_path = {a.path: a for a in arts}

    assert by_path["af2/ranked_0.pdb"].kind == "pdb"
    assert by_path["af2/ranked_0.pdb"].stage == "af2"
    assert by_path["msa/aln.a3m"].kind == "a3m"
    assert by_path["af2/scores.json"].size_bytes > 0
    #  관리 파일은 들어가지 않는다
    assert "request.json" not in by_path


async def test_이벤트_순번과_시각을_살린다(repos: Repos, legacy_root: Path):
    evs = None
    await LegacyMigrator(repos).migrate_root(legacy_root)
    evs = await repos.events.list("run-20260901-abc")

    assert [e.seq for e in evs] == [1, 2, 3]
    assert evs[0].message == "MSA 시작"
    assert evs[0].created_at.year == 2026
    assert evs[2].level == "error"


async def test_피드백의_표기_흔들림을_정규화한다(repos: Repos, legacy_root: Path):
    """현행은 verdict·rating, good·bad 를 섞어 쓴다."""
    await LegacyMigrator(repos).migrate_root(legacy_root)

    rows = await repos.feedback.list("run-20260901-abc")
    verdicts = {r["subject_id"]: r["verdict"] for r in rows}
    assert verdicts["seq-1"] == "positive"
    assert verdicts["seq-2"] == "negative"


async def test_다시_돌려도_중복되지_않는다(repos: Repos, legacy_root: Path):
    """멱등 — 마이그레이션은 여러 번 돌 수 있어야 한다."""
    m = LegacyMigrator(repos)
    await m.migrate_root(legacy_root)
    second = await m.migrate_root(legacy_root)

    assert second.runs == 0
    assert second.runs_skipped == 2
    assert await repos.runs.count() == 2
    assert len(await repos.events.list("run-20260901-abc")) == 3


async def test_overwrite_는_다시_넣는다(repos: Repos, legacy_root: Path):
    m = LegacyMigrator(repos)
    await m.migrate_root(legacy_root)
    again = await m.migrate_root(legacy_root, overwrite=True)

    assert again.runs == 2
    assert await repos.runs.count() == 2


async def test_원본을_지우지_않는다(repos: Repos, legacy_root: Path):
    """마이그레이션이 잘못되어도 되돌릴 수 있어야 한다."""
    before = sorted(p.name for p in (legacy_root / "run-20260901-abc").iterdir())

    await LegacyMigrator(repos).migrate_root(legacy_root)

    after = sorted(p.name for p in (legacy_root / "run-20260901-abc").iterdir())
    assert before == after


async def test_경로가_없으면_오류를_보고하고_죽지_않는다(repos: Repos, tmp_path: Path):
    report = await LegacyMigrator(repos).migrate_root(tmp_path / "없는경로")

    assert report.runs == 0
    assert len(report.errors) == 1


async def test_프로젝트와_라운드를_옮긴다(repos: Repos, legacy_root: Path):
    """현행 workspace/projects 구조를 승계한다."""
    await LegacyMigrator(repos).migrate_root(legacy_root)

    proj = await repos.projects.get("proj-1")
    assert proj.name == "라이소자임 안정화"
    assert proj.tags == ["효소"]

    rnd = await repos.rounds.get("round-1")
    assert rnd.project_id == "proj-1"
    assert rnd.linked_run_ids == ["run-20260901-abc"]
