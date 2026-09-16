"""Execution service: the join between the DAG engine and the job queue.

The engine decides what should run; the repositories hold the state. This
module is what turns those two into a run.

    workflow -> validate graph -> create run -> enqueue what is ready
                                    ↑                      ↓
                                    └── a finished node enqueues the next

Work advances one level at a time, so nodes that may run in parallel reach the
queue together.
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
    """Owns the lifecycle of a run."""

    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.router = ModelRouter(repos.models)

    # ------------------------------------------------------------ start

    async def preflight(self, workflow: Workflow, *, max_gpu: int | None = None) -> dict[str, Any]:
        """Preflight: collect graph defects and unresolvable models up front."""
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
        """Start a run from a workflow and enqueue whatever is ready."""
        graph = build_graph(workflow)  # a defective graph stops here

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
        await self.repos.events.append(run.run_id, "실행을 시작했습니다")
        await self.repos.audit.record(
            "run.create", actor_id=owner_id, target_type="run", target_id=run.run_id,
            detail={"workflow_id": workflow.workflow_id, "version": workflow.version},
        )

        if round_id:
            await self.repos.rounds.link_runs(round_id, [run.run_id])

        plan = ExecutionPlan.start(graph)
        await self._enqueue_ready(run.run_id, graph, plan)

        #  When the first node cannot be routed the run is already over. Close
        #  it rather than leave it sitting in RUNNING with nothing to do.
        if plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run.run_id, final)
            await self.repos.events.append(run.run_id, f"실행이 끝났습니다 ({final})")

        return await self.repos.runs.get(run.run_id) or run

    # ------------------------------------------------------------ progress

    async def load_plan(self, run_id: str) -> tuple[Graph, ExecutionPlan] | None:
        """Rebuild the execution plan from stored state.

        Workers hold nothing between jobs; the database is the only source.
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
        """A node finished. Record it and enqueue whatever that unblocked."""
        loaded = await self.load_plan(run_id)
        if loaded is None:
            return {"ok": False, "error": "실행 계획을 복원하지 못했습니다"}
        graph, plan = loaded

        if node_id not in plan.states:
            return {"ok": False, "error": f"그래프에 없는 노드입니다: {node_id}"}

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
                run_id, f"{node_id} 실패했습니다: {error}", stage=node_id, level="error",
            )

        #  Persist skipped nodes so a screen can say why they were skipped
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
        """Enqueue the nodes that are ready, skipping any already queued."""
        existing = {
            j.node_id for j in await self.repos.jobs.list_for_run(run_id) if j.node_id
        }
        queued: list[str] = []
        handled: set[str] = set()

        #  Passing a control node makes others ready, so the loop runs again.
        #  It ends when nothing more can pass. Recursion here would descend
        #  forever on the same node.
        while True:
            passed_control = False

            for node_id in plan.ready():
                if node_id in existing or node_id in handled:
                    continue
                node = graph.nodes[node_id]

                #  Nodes with no external call pass immediately. A BRANCH only
                #  evaluates a condition; FANOUT and JOIN only shape the flow.
                #  None names a model, so queueing one would produce a job with
                #  no route and a worker failure to go with it.
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

                #  Resource requirements come from the registry and drive scheduling
                if node.kind is NodeKind.MODEL and node.model_id:
                    try:
                        route = await self.router.route(node.model_id, node.model_version)
                        job.resources = route.resources
                        job.payload["route"] = route.as_dict()
                    except RoutingError as exc:
                        #  A routing failure is that node failing. Record it and move on.
                        await self.repos.events.append(
                            run_id, f"{node_id} 라우팅에 실패했습니다: {exc}", stage=node_id, level="error",
                        )
                        plan.mark_failed(node_id, str(exc))
                        await self.repos.runs.upsert_stage(run_id, StageState(
                            name=node_id, status=RunStatus.FAILED, error=str(exc),
                        ))
                        passed_control = True  # skips may have propagated
                        continue

                await self.repos.jobs.enqueue(job)
                await self.repos.runs.upsert_stage(run_id, StageState(
                    name=node_id, status=RunStatus.PENDING,
                ))
                queued.append(node_id)

            if not passed_control:
                break

        #  Persist whatever the skip propagation touched
        for node_id, state in plan.states.items():
            if state.outcome is NodeOutcome.SKIPPED:
                await self.repos.runs.upsert_stage(run_id, StageState(
                    name=node_id, status=RunStatus.CANCELLED, error=state.reason,
                ))

        return queued

    # ------------------------------------------------------------ cancel

    async def cancel(self, run_id: str, *, reason: str = "사용자 취소") -> Run | None:
        """Cancel a run and drop whatever it left on the queue."""
        for job in await self.repos.jobs.list_for_run(run_id):
            if job.status in ("queued", "leased", "running"):
                await self.repos.jobs.finish(job.job_id, status="cancelled", error=reason)
        await self.repos.events.append(run_id, f"실행을 취소했습니다: {reason}", level="warning")
        return await self.repos.runs.set_status(run_id, RunStatus.CANCELLED, error=reason)
