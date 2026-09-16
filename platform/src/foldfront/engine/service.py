"""실행 서비스 — DAG 엔진과 작업 큐를 잇는다.

엔진(engine/dag.py)은 무엇을 돌릴지만 정하고, 저장소(db/repositories.py)는 상태를 담는다.
이 모듈이 둘을 이어 실제 실행 흐름을 만든다.

    워크플로 → 그래프 검증 → run 생성 → 준비된 노드를 큐에 넣는다
                                    ↑                      ↓
                                    └── 워커가 끝내면 다음 노드를 넣는다

한 번에 한 층씩 진행하므로 병렬 노드는 동시에 큐에 들어간다.
"""

from __future__ import annotations

from typing import Any

from foldfront.db.models import (
    Job,
    NodeKind,
    Run,
    RunStatus,
    StageState,
    Workflow,
    WorkflowNode,
)
from foldfront.db.repositories import Repos, new_id
from foldfront.engine.dag import (
    ExecutionPlan,
    Graph,
    GraphError,
    NodeOutcome,
    build_graph,
)
from foldfront.engine.router import ModelRouter, RoutingError


class ExecutionService:
    """워크플로 실행을 관장한다."""

    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.router = ModelRouter(repos.models)

    # ------------------------------------------------------------ 시작

    async def preflight(self, workflow: Workflow, *, max_gpu: int | None = None) -> dict[str, Any]:
        """실행 전 점검. 그래프 결함과 모델 해석 실패를 미리 모아 알린다."""
        try:
            graph = build_graph(workflow)
        except GraphError as exc:
            return {"ok": False, "graph_error": str(exc), "resolved": [], "errors": [str(exc)]}

        wanted = [
            (n.model_id, n.model_version)
            for n in graph.nodes.values()
            if n.kind is NodeKind.MODEL and n.model_id
        ]
        result = await self.router.preflight(wanted, max_gpu=max_gpu)
        result["graph_error"] = None
        result["node_count"] = len(graph.nodes)
        result["levels"] = [list(layer) for layer in graph.levels]
        return result

    async def start(
        self,
        workflow: Workflow,
        *,
        request: dict[str, Any] | None = None,
        project_id: str | None = None,
        round_id: str | None = None,
        owner_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        """워크플로로 run 을 시작한다. 준비된 노드를 큐에 넣는다."""
        graph = build_graph(workflow)  # 결함이 있으면 여기서 멈춘다

        run = Run(
            run_id=run_id or new_id("run"),
            mode="workflow",
            status=RunStatus.RUNNING,
            request=dict(request or {}),
            project_id=project_id,
            round_id=round_id,
            owner_id=owner_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            stages=[StageState(name=n) for n in graph.order],
        )
        await self.repos.runs.create(run)
        await self.repos.runs.set_status(run.run_id, RunStatus.RUNNING)
        await self.repos.events.append(run.run_id, "실행을 시작했다")
        await self.repos.audit.record(
            "run.create", actor_id=owner_id, target_type="run", target_id=run.run_id,
            detail={"workflow_id": workflow.workflow_id, "version": workflow.version},
        )

        if round_id:
            await self.repos.rounds.link_runs(round_id, [run.run_id])

        plan = ExecutionPlan.start(graph)
        await self._enqueue_ready(run.run_id, graph, plan)

        #  첫 노드부터 라우팅에 실패하면 시작하자마자 끝난다 — RUNNING 으로 방치하지 않는다
        if plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run.run_id, final)
            await self.repos.events.append(run.run_id, f"실행이 끝났다 ({final})")

        return await self.repos.runs.get(run.run_id) or run

    # ------------------------------------------------------------ 진행

    async def load_plan(self, run_id: str) -> tuple[Graph, ExecutionPlan] | None:
        """저장된 상태에서 실행 계획을 복원한다.

        워커는 상태를 들고 있지 않는다 — DB 가 유일한 원천이다.
        """
        run = await self.repos.runs.get(run_id)
        if run is None or not run.workflow_id:
            return None
        workflow = await self.repos.workflows.get(run.workflow_id, run.workflow_version)
        if workflow is None:
            return None

        graph = build_graph(workflow)
        plan = ExecutionPlan.start(graph)

        for stage in run.stages:
            if stage.name not in plan.states:
                continue
            state = plan.states[stage.name]
            if stage.status is RunStatus.SUCCEEDED:
                state.outcome = NodeOutcome.SUCCEEDED
                state.result = dict(stage.metrics)
                plan.context[stage.name] = state.result
                node = graph.nodes[stage.name]
                if node.model_id:
                    plan.context.setdefault(node.model_id, state.result)
            elif stage.status is RunStatus.FAILED:
                state.outcome = NodeOutcome.FAILED
                state.reason = stage.error
            elif stage.status is RunStatus.CANCELLED:
                state.outcome = NodeOutcome.SKIPPED
                state.reason = stage.error
            elif stage.status is RunStatus.RUNNING:
                state.outcome = NodeOutcome.RUNNING

        return graph, plan

    async def complete_node(
        self,
        run_id: str,
        node_id: str,
        *,
        succeeded: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """노드 하나가 끝났다. 상태를 갱신하고 다음 노드를 큐에 넣는다."""
        loaded = await self.load_plan(run_id)
        if loaded is None:
            return {"ok": False, "error": "실행 계획을 복원하지 못했다"}
        graph, plan = loaded

        if node_id not in plan.states:
            return {"ok": False, "error": f"그래프에 없는 노드다: {node_id}"}

        if succeeded:
            plan.mark_succeeded(node_id, result or {})
            await self.repos.runs.upsert_stage(run_id, StageState(
                name=node_id, status=RunStatus.SUCCEEDED, metrics=dict(result or {}),
            ))
            await self.repos.events.append(run_id, f"{node_id} 완료", stage=node_id)
        else:
            plan.mark_failed(node_id, error)
            await self.repos.runs.upsert_stage(run_id, StageState(
                name=node_id, status=RunStatus.FAILED, error=error,
            ))
            await self.repos.events.append(
                run_id, f"{node_id} 실패: {error}", stage=node_id, level="error",
            )

        #  건너뛰기가 전파된 노드를 DB 에도 반영한다 — 화면이 이유를 보여준다
        for other_id, state in plan.states.items():
            if state.outcome is NodeOutcome.SKIPPED:
                await self.repos.runs.upsert_stage(run_id, StageState(
                    name=other_id, status=RunStatus.CANCELLED, error=state.reason,
                ))

        queued = await self._enqueue_ready(run_id, graph, plan)

        if plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run_id, final)
            await self.repos.events.append(run_id, f"실행이 끝났다 ({final})")

        return {
            "ok": True,
            "queued": queued,
            "done": plan.done(),
            "summary": plan.summary(),
        }

    async def _enqueue_ready(
        self, run_id: str, graph: Graph, plan: ExecutionPlan
    ) -> list[str]:
        """지금 실행할 수 있는 노드를 큐에 넣는다. 이미 큐에 있는 것은 넣지 않는다."""
        existing = {
            j.node_id for j in await self.repos.jobs.list_for_run(run_id) if j.node_id
        }
        queued: list[str] = []
        handled: set[str] = set()

        #  제어 노드를 통과시키면 새 노드가 준비되므로 다시 돈다. 더 통과시킬 것이
        #  없으면 끝난다 — 재귀로 두면 같은 노드를 두고 무한히 내려간다.
        while True:
            passed_control = False

            for node_id in plan.ready():
                if node_id in existing or node_id in handled:
                    continue
                node = graph.nodes[node_id]

                #  외부 호출이 필요 없는 노드는 큐를 거치지 않고 즉시 통과시킨다.
                #  BRANCH 는 조건만 평가하고 FANOUT·JOIN 은 흐름만 가른다 — 셋 다 모델이
                #  없으므로 큐에 넣으면 라우팅 정보가 없는 작업이 되어 워커가 실패한다.
                if node.kind in (NodeKind.FANOUT, NodeKind.JOIN, NodeKind.BRANCH) or (
                    node.kind is NodeKind.MODEL and not node.model_id
                ):
                    await self.repos.runs.upsert_stage(run_id, StageState(
                        name=node_id, status=RunStatus.SUCCEEDED,
                    ))
                    plan.mark_succeeded(node_id, {})
                    handled.add(node_id)
                    passed_control = True
                    continue

                handled.add(node_id)

                job = Job(
                    job_id=new_id("job"),
                    run_id=run_id,
                    node_id=node_id,
                    model_id=node.model_id,
                    model_version=node.model_version,
                    payload={"params": dict(node.params), "kind": str(node.kind)},
                )

                #  자원 요구량은 Registry 에서 가져온다(스케줄링 근거)
                if node.kind is NodeKind.MODEL and node.model_id:
                    try:
                        route = await self.router.route(node.model_id, node.model_version)
                        job.resources = route.resources
                        job.payload["route"] = route.as_dict()
                    except RoutingError as exc:
                        #  라우팅 실패는 그 노드의 실패다. 계획에 바로 반영하고 넘어간다.
                        await self.repos.events.append(
                            run_id, f"{node_id} 라우팅 실패: {exc}", stage=node_id, level="error",
                        )
                        plan.mark_failed(node_id, str(exc))
                        await self.repos.runs.upsert_stage(run_id, StageState(
                            name=node_id, status=RunStatus.FAILED, error=str(exc),
                        ))
                        passed_control = True  # 건너뛰기가 전파됐을 수 있다
                        continue

                await self.repos.jobs.enqueue(job)
                await self.repos.runs.upsert_stage(run_id, StageState(
                    name=node_id, status=RunStatus.PENDING,
                ))
                queued.append(node_id)

            if not passed_control:
                break

        #  건너뛰기가 전파된 노드를 DB 에 반영한다
        for node_id, state in plan.states.items():
            if state.outcome is NodeOutcome.SKIPPED:
                await self.repos.runs.upsert_stage(run_id, StageState(
                    name=node_id, status=RunStatus.CANCELLED, error=state.reason,
                ))

        return queued

    # ------------------------------------------------------------ 취소

    async def cancel(self, run_id: str, *, reason: str = "사용자 취소") -> Run | None:
        """실행을 취소한다. 큐에 남은 작업도 함께 거둔다."""
        for job in await self.repos.jobs.list_for_run(run_id):
            if job.status in ("queued", "leased", "running"):
                await self.repos.jobs.finish(job.job_id, status="cancelled", error=reason)
        await self.repos.events.append(run_id, f"실행을 취소했다: {reason}", level="warning")
        return await self.repos.runs.set_status(run_id, RunStatus.CANCELLED, error=reason)
