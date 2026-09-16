"""운영·시연용 명령.

    uv run python -m foldfront.cli seed        # 기본 모델·워크플로 등록
    uv run python -m foldfront.cli demo        # 모의 실행 한 벌
    uv run python -m foldfront.cli worker      # 워커 기동
    uv run python -m foldfront.cli migrate <경로>   # 레거시 이관
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from foldfront.core.config import get_settings
from foldfront.db.client import ensure_indexes
from foldfront.db.legacy import LegacyMigrator
from foldfront.db.models import (
    Artifact,
    ModelKind,
    ModelVersion,
    NodeKind,
    ResourceSpec,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from foldfront.db.repositories import Repos
from foldfront.engine.adapters import AdapterRegistry
from foldfront.engine.dag import BUILTIN_STAGE_CHAIN, builtin_pipeline_workflow
from foldfront.engine.service import ExecutionService
from foldfront.engine.worker import Worker

#  현행 RAPID 가 환경변수로 읽던 모델을 Registry 항목으로 옮긴다.
SEED_MODELS = [
    ("mmseqs", ModelKind.MSA, 0),
    ("msa", ModelKind.MSA, 0),
    ("rfd3", ModelKind.BACKBONE, 1),
    ("bioemu", ModelKind.BACKBONE, 1),
    ("design", ModelKind.SEQUENCE, 1),
    ("proteinmpnn", ModelKind.SEQUENCE, 1),
    ("soluprot", ModelKind.SOLUBILITY, 0),
    ("af2", ModelKind.STRUCTURE, 1),
    ("colabfold", ModelKind.STRUCTURE, 1),
    ("diffdock", ModelKind.DOCKING, 1),
    ("novelty", ModelKind.OTHER, 0),
]


def binding_workflow() -> Workflow:
    """단백질 결합 예측 파이프라인.

    안정화 산출물을 입력으로 도킹·복합체 예측을 조합한다. 조건 분기로 용해도 통과율이
    낮으면 도킹을 건너뛴다 — 계산 자원을 아끼는 실제 운영 방식이다.
    """
    return Workflow(
        workflow_id="binding-prediction",
        name="결합 예측",
        description="설계 서열을 입력으로 도킹과 복합체 구조 예측을 수행한다",
        is_template=True,
        nodes=[
            WorkflowNode(node_id="msa", kind=NodeKind.MODEL, model_id="msa",
                         position={"x": 0, "y": 80}),
            WorkflowNode(node_id="design", kind=NodeKind.MODEL, model_id="design",
                         position={"x": 180, "y": 80}),
            WorkflowNode(node_id="soluprot", kind=NodeKind.MODEL, model_id="soluprot",
                         position={"x": 360, "y": 80}),
            WorkflowNode(node_id="gate", kind=NodeKind.BRANCH,
                         condition="soluprot.pass_rate > 0.3", label="용해도 통과",
                         position={"x": 540, "y": 80}),
            WorkflowNode(node_id="split", kind=NodeKind.FANOUT,
                         position={"x": 700, "y": 80}),
            WorkflowNode(node_id="diffdock", kind=NodeKind.MODEL, model_id="diffdock",
                         position={"x": 860, "y": 10}),
            WorkflowNode(node_id="af2", kind=NodeKind.MODEL, model_id="af2",
                         position={"x": 860, "y": 150}),
            WorkflowNode(node_id="merge", kind=NodeKind.JOIN,
                         position={"x": 1030, "y": 80}),
            WorkflowNode(node_id="novelty", kind=NodeKind.MODEL, model_id="novelty",
                         position={"x": 1190, "y": 80}),
            WorkflowNode(node_id="stop", kind=NodeKind.MODEL, model_id="novelty",
                         label="조기 종료", position={"x": 700, "y": 230}),
        ],
        edges=[
            WorkflowEdge(source="msa", target="design"),
            WorkflowEdge(source="design", target="soluprot"),
            WorkflowEdge(source="soluprot", target="gate"),
            WorkflowEdge(source="gate", target="split", branch="true"),
            WorkflowEdge(source="gate", target="stop", branch="false"),
            WorkflowEdge(source="split", target="diffdock"),
            WorkflowEdge(source="split", target="af2"),
            WorkflowEdge(source="diffdock", target="merge"),
            WorkflowEdge(source="af2", target="merge"),
            WorkflowEdge(source="merge", target="novelty"),
        ],
    )


async def cmd_seed() -> None:
    repos = Repos()
    await ensure_indexes()
    for model_id, kind, gpu in SEED_MODELS:
        await repos.models.register(ModelVersion(
            model_id=model_id, version="v1", kind=kind,
            endpoint_id=f"ep-{model_id}", active=True, is_default=True,
            resources=ResourceSpec(gpu_count=gpu, gpu_memory_gb=24.0 if gpu else None),
        ))
    await repos.workflows.save(builtin_pipeline_workflow())
    await repos.workflows.save(binding_workflow())
    print(f"모델 {len(SEED_MODELS)}종 · 워크플로 2종을 등록했다")


#  원본 RAPID 사례연구의 실제 설계 결과다(public_data/case_studies/multiround).
#  모의 실행에 이 파일을 붙여 구조 열람 화면이 빈 채로 남지 않게 한다.
#  ★ 지어낸 구조가 아니라 원본 산출물이며, 산출물 meta 에 출처를 남긴다.
CASE_STUDIES = ("1lvm", "3rgk")


async def _attach_case_artifacts(repos: Repos, run_id: str, case: str) -> int:
    """사례연구 PDB 를 run 의 산출물로 등록한다. 실제 실행이 쓰는 경로 구조를 그대로 따른다."""
    src_dir = Path(__file__).resolve().parents[3] / "public_data" / "case_studies" / "multiround" / case
    if not src_dir.is_dir():
        return 0

    root = Path(get_settings().output_root).resolve()
    attached = 0
    for src in sorted(src_dir.glob("*.pdb")):
        rel = Path(run_id) / "af2" / src.name
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(src, dst)
        await repos.artifacts.register(
            Artifact(
                run_id=run_id,
                stage="af2",
                path=str(rel),
                kind="pdb",
                size_bytes=dst.stat().st_size,
                content_type="chemical/x-pdb",
                meta={"source": f"RAPID case study {case}", "mock_run": True},
            )
        )
        attached += 1
    return attached


async def cmd_demo(count: int = 3) -> None:
    """모의 실행으로 화면에 채울 자료를 만든다.

    ⚠️ 모의 결과다. 실증은 실제 엔드포인트로 한다.
    """
    repos = Repos()
    svc = ExecutionService(repos)
    worker = Worker(repos=repos, adapters=AdapterRegistry(mock=True))

    for i in range(count):
        wf_id = "binding-prediction" if i % 2 else "builtin-pipeline"
        wf = await repos.workflows.get(wf_id)
        if wf is None:
            print(f"워크플로가 없다: {wf_id} — 먼저 seed 를 돌린다")
            return
        run = await svc.start(wf, request={
            "target_fasta": f"/data/targets/sample{i + 1}.fasta",
            "target_pdb": f"/data/targets/sample{i + 1}.pdb",
            "design_chains": ["A"],
        })
        await worker.drain()
        attached = await _attach_case_artifacts(repos, run.run_id, CASE_STUDIES[i % len(CASE_STUDIES)])
        final = await repos.runs.get(run.run_id)
        print(f"  {run.run_id} · {wf_id} · {final.status} · 산출물 {attached}건")

    print(f"모의 실행 {count}건 완료 · {worker.stats.as_dict()}")
    print("⚠️ 지표는 모의 어댑터 값이다. 붙은 PDB 는 원본 RAPID 사례연구의 실제 산출물이다.")


async def cmd_worker(mock: bool = False) -> None:
    repos = Repos()
    worker = Worker(repos=repos, adapters=AdapterRegistry(mock=mock))
    print(f"워커 시작 {worker.worker_id} (모의={mock}) — Ctrl+C 로 멈춘다")
    await worker.run_forever()


async def cmd_migrate(path: str) -> None:
    repos = Repos()
    await ensure_indexes()
    report = await LegacyMigrator(repos).migrate_root(path)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


async def cmd_status() -> None:
    repos = Repos()
    runs = await repos.runs.count()
    models = len(await repos.models.list())
    workflows = len(await repos.workflows.list())
    jobs = await repos.jobs.stats()
    print(json.dumps({
        "runs": runs, "models": models, "workflows": workflows, "jobs": jobs,
        "stage_chain": list(BUILTIN_STAGE_CHAIN),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]

    if cmd == "seed":
        asyncio.run(cmd_seed())
    elif cmd == "demo":
        asyncio.run(cmd_demo(int(rest[0]) if rest else 3))
    elif cmd == "worker":
        asyncio.run(cmd_worker(mock="--mock" in rest))
    elif cmd == "migrate":
        if not rest:
            print("경로가 필요하다")
            return
        asyncio.run(cmd_migrate(rest[0]))
    elif cmd == "status":
        asyncio.run(cmd_status())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
