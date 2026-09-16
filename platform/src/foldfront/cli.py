"""Operational and demonstration commands.

    uv run python -m foldfront.cli seed          register default models and workflows
    uv run python -m foldfront.cli demo          produce a set of mock runs
    uv run python -m foldfront.cli worker        start a worker
    uv run python -m foldfront.cli migrate PATH  import an existing output directory
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

#  The models the original reads from the environment, as registry entries.
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
    """A binding-prediction pipeline.

    Stabilised designs feed docking and complex prediction. A branch skips the
    docking when the solubility pass rate is low, which is how the expensive
    stages are kept off candidates that will not survive anyway.
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


#  Real design results from the original case studies, under
#  public_data/case_studies/multiround. Attaching them to a mock run gives the
#  structure viewer something genuine to show. They are not invented, and each
#  artifact records where it came from in its meta.
CASE_STUDIES = ("1lvm", "3rgk")


async def _attach_case_artifacts(repos: Repos, run_id: str, case: str) -> int:
    """Register the case-study PDBs as artifacts of a run, laid out exactly
    as a real run would write them."""
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
    """Produce mock runs so the screens have something to show.

    The metrics are invented. Only a call to a real endpoint demonstrates
    anything, and the command says so when it finishes.
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
