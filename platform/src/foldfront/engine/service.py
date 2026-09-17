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

import re

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
        await self._link_inputs(run.run_id, run.request)

        plan = ExecutionPlan.start(graph)
        await self._enqueue_ready(run.run_id, graph, plan, request=dict(run.request or {}))

        #  When the first node cannot be routed the run is already over. Close
        #  it rather than leave it sitting in RUNNING with nothing to do.
        if plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run.run_id, final)
            await self.repos.events.append(run.run_id, f"실행이 끝났습니다 ({final})")

        return await self.repos.runs.get(run.run_id) or run

    async def fork(self, run_id: str, *, from_stage: str | None = None) -> Run | None:
        """A new run that inherits what the original finished before a stage.

        What it inherits is decided from the graph, not from the order stages
        happen to be listed in: the fork point's ancestors must all have
        succeeded, and only those come along. A sibling on another branch -
        failed, or skipped because a condition went the other way - is neither
        a reason to refuse nor something to inherit; it is simply re-derived.
        """
        run = await self.repos.runs.get(run_id)
        if run is None:
            return None
        if not from_stage:
            return await self.repos.runs.fork(run_id, keep_names=[])
        workflow = await self.repos.workflows.get(run.workflow_id, run.workflow_version) if run.workflow_id else None
        if workflow is None:
            raise ValueError("워크플로를 찾지 못해 갈라질 수 없습니다")
        graph = build_graph(workflow)
        if from_stage not in graph.nodes:
            raise ValueError(f"그런 단계가 없습니다: {from_stage}")

        ancestors: set[str] = set()
        frontier = list(graph.parents(from_stage))
        while frontier:
            n = frontier.pop()
            if n in ancestors:
                continue
            ancestors.add(n)
            frontier.extend(graph.parents(n))
        status = {st.name: st.status for st in run.stages}
        for name in sorted(ancestors):
            if status.get(name) is not RunStatus.SUCCEEDED:
                raise ValueError(f"{name} 단계가 끝나지 않아 {from_stage} 부터 갈라질 수 없습니다")
        return await self.repos.runs.fork(run_id, from_stage=from_stage, keep_names=sorted(ancestors))

    async def resume(self, run_id: str, *, actor_id: str | None = None) -> Run | None:
        """Start a run that exists but has not begun - a fork, waiting.

        A fork inherits the stages before the point it forked from, as
        succeeded, and nothing after. Starting it is rebuilding the plan from
        those stages and queueing what they make ready - the same step the
        original took at its own start, from further along.
        """
        run = await self.repos.runs.get(run_id)
        if run is None:
            return None
        #  One starter. A second click, or a cancel that landed first, finds
        #  nothing PENDING to claim and changes nothing.
        claimed = await self.repos.runs.claim_start(run_id)
        if claimed is None:
            return run

        loaded = await self.load_plan(run_id)
        if loaded is None:
            await self.repos.runs.set_status(run_id, RunStatus.FAILED, error="실행 계획을 복원하지 못했습니다")
            return await self.repos.runs.get(run_id)
        graph, plan = loaded

        await self.repos.events.append(
            run_id,
            f"{run.forked_from_stage} 단계부터 다시 시작했습니다" if run.forked_from_stage else "실행을 시작했습니다",
        )
        await self.repos.audit.record(
            "run.start", actor_id=actor_id, target_type="run", target_id=run_id,
            detail={"forked_from": run.forked_from_run_id, "from_stage": run.forked_from_stage},
        )
        if run.round_id:
            await self.repos.rounds.link_runs(run.round_id, [run_id])
        await self._link_inputs(run_id, run.request)

        queued = await self._enqueue_ready(run_id, graph, plan, request=dict(run.request or {}))
        if plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run_id, final)
            await self.repos.events.append(run_id, f"실행이 끝났습니다 ({final})")
        elif not queued:
            #  Nothing ready and nothing running is a run that will never move.
            #  Closing it here is better than a stalled notice in half an hour.
            await self.repos.runs.set_status(run_id, RunStatus.FAILED, error="시작할 수 있는 노드가 없습니다")
            await self.repos.events.append(run_id, "시작할 수 있는 노드가 없어 닫았습니다", level="error")
        return await self.repos.runs.get(run_id)

    async def _link_inputs(self, run_id: str, request: dict[str, Any] | None) -> int:
        """Tie the uploaded files a request names to the run that reads them.

        Here, not in one route: runs start from the API, from MCP, from the CLI
        and from a fork, and a file is that run's provenance whichever way it
        was started. Only top-level values are looked at, because that is all
        StageInput.path ever reads - linking a file under a nested key would
        record as read something no stage reads. Values are resolved the way
        StageInput.text resolves them, so a relative spelling links the same
        file the run will read.
        """
        from pathlib import Path

        from foldfront.core.config import get_settings

        root = Path(get_settings().output_root).resolve()
        paths: list[str] = []

        for v in (request or {}).values():
            if not isinstance(v, str):
                continue
            raw = v.strip()
            if raw and not raw.startswith(">") and not re.search(r"\s", raw):
                target = (root / raw).resolve()
                if target.is_relative_to(root):
                    paths.append(str(target))
        return await self.repos.inputs.link_run(paths, run_id) if paths else 0

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
        run = await self.repos.runs.get(run_id)
        if run is None:
            return {"ok": False, "error": "실행을 찾지 못했습니다"}
        if run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
            #  A worker can finish a job after its run was cancelled, or after
            #  a reconcile closed the run because the lease had expired. What
            #  was reported stays reported; the late result is noted, not applied.
            await self.repos.events.append(
                run_id, f"{node_id} 의 결과가 늦게 도착했으나 실행은 이미 {run.status} 로 끝나 반영하지 않습니다",
                stage=node_id, level="warning",
            )
            return {"ok": False, "late": True, "error": f"실행이 이미 끝났습니다: {run.status}"}
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
        self, run_id: str, graph: Graph, plan: ExecutionPlan,
        request: dict[str, Any] | None = None,
    ) -> list[str]:
        """Enqueue the nodes that are ready, skipping any already queued.

        `request` is what the run was asked for. A node builds its input from
        that, from what it declares itself, and from what earlier stages
        produced, so it has to travel with the job rather than be looked up.
        """
        if request is None:
            stored = await self.repos.runs.get(run_id)
            request = dict(stored.request) if stored else {}

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

                #  A node needs three things to build its input: what the node
                #  itself declares, what the run was asked for, and what earlier
                #  stages produced. Sending only the first is why this worked
                #  under the mock adapter, which reads none of them, and failed
                #  the moment a real endpoint wanted a sequence.
                job = Job(
                    job_id=new_id("job"),
                    run_id=run_id,
                    node_id=node_id,
                    model_id=node.model_id,
                    model_version=node.model_version,
                    payload={
                        "params": {**request, **dict(node.params)},
                        "upstream": plan.context_for(node_id),
                        "kind": str(node.kind),
                    },
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

                if await self.repos.jobs.enqueue(job) is None:
                    #  Someone else queued this node between our read of
                    #  `existing` and this insert. Their job is the job.
                    continue
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

    # ------------------------------------------------------------ repair

    async def reconcile(self, run_id: str) -> dict[str, Any]:
        """Bring a run back in step with its jobs.

        A run and its jobs are written separately, so they can disagree. Three
        ways, all of which leave a run that will never move again:

        - A job finished but the run was never told. The worker records the
          job and then the stage; a worker that dies between those two writes
          leaves the work done and the run still waiting for it.
        - A lease expired past the retry limit. `reclaim_expired` fails the
          job, but it works on jobs alone and has no way to fail the run the
          job belonged to.
        - A job went missing. Whatever the cause, a run whose next node has no
          job is waiting for something that will never arrive.

        Nothing here invents an outcome. A stage is only moved to match a job
        that actually recorded one, and a node with no job is queued again -
        which is what would have happened had the job never been lost.

        Only a RUNNING run is repaired. A PENDING run was never started - a
        fork waiting for someone to start it, for one - and queueing its work
        would be starting it on that person's behalf. A finished run is
        history: moving it would rewrite what was reported.
        """
        run = await self.repos.runs.get(run_id)
        if run is None:
            return {"ok": False, "error": "실행을 찾지 못했습니다"}
        if run.status is not RunStatus.RUNNING:
            return {"ok": True, "run_id": run_id, "status": str(run.status), "repaired": []}

        stages = {s.name: s for s in run.stages}
        jobs = await self.repos.jobs.list_for_run(run_id)
        repaired: list[dict[str, Any]] = []
        #  A node with a live job is a worker's to finish. A dead twin of it -
        #  possible before the unique index existed - must not be read as the
        #  node's outcome while the live one is still running.
        live_nodes = {j.node_id for j in jobs if str(j.status) in ("queued", "leased", "running")}

        for job in jobs:
            if not job.node_id or job.node_id in live_nodes:
                continue
            stage = stages.get(job.node_id)
            settled = stage is not None and stage.status in (
                RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED
            )
            if settled:
                continue

            if str(job.status) == "succeeded":
                #  None means the result was never recorded; {} means the model
                #  really replied with nothing. The first fails rather than
                #  feeding nothing onward, the second is recorded as it came.
                if job.result is not None:
                    await self.complete_node(
                        run_id, job.node_id, succeeded=True, result=dict(job.result),
                    )
                    repaired.append({"node_id": job.node_id, "did": "결과를 실행에 기록했습니다"})
                else:
                    await self.complete_node(
                        run_id, job.node_id, succeeded=False,
                        error="작업은 끝났으나 결과가 남지 않았습니다. 다시 실행하십시오.",
                    )
                    repaired.append({"node_id": job.node_id, "did": "결과를 잃어 실패로 표시했습니다"})
            elif str(job.status) in ("failed", "cancelled"):
                await self.complete_node(
                    run_id, job.node_id, succeeded=False,
                    error=job.error or "작업이 끝나지 못했습니다",
                )
                repaired.append({"node_id": job.node_id, "did": "작업 실패를 실행에 반영했습니다"})

        #  Re-read: completing a node above may have finished the run.
        run = await self.repos.runs.get(run_id)
        if run is None or run.status is not RunStatus.RUNNING:
            return {"ok": True, "run_id": run_id,
                    "status": str(run.status) if run else "unknown", "repaired": repaired}

        alive = [j for j in await self.repos.jobs.list_for_run(run_id)
                 if str(j.status) in ("queued", "leased", "running")]
        if alive:
            return {"ok": True, "run_id": run_id, "status": str(run.status), "repaired": repaired}

        #  Still live with nothing on the queue. Whatever it was waiting for is
        #  not coming, so work out what should be running and queue that.
        loaded = await self.load_plan(run_id)
        if loaded is None:
            await self.repos.runs.set_status(
                run_id, RunStatus.FAILED, error="실행 계획을 복원하지 못했습니다",
            )
            repaired.append({"node_id": None, "did": "계획을 복원하지 못해 실패로 닫았습니다"})
            return {"ok": True, "run_id": run_id, "status": "failed", "repaired": repaired}

        graph, plan = loaded
        queued = await self._enqueue_ready(run_id, graph, plan)
        if queued:
            await self.repos.events.append(
                run_id, f"정합성 점검으로 {', '.join(queued)} 을(를) 다시 큐에 넣었습니다",
                level="warning",
            )
            repaired.append({"node_id": ", ".join(queued), "did": "잃어버린 작업을 다시 큐에 넣었습니다"})
        elif plan.done():
            final = RunStatus.SUCCEEDED if plan.succeeded() else RunStatus.FAILED
            await self.repos.runs.set_status(run_id, final)
            await self.repos.events.append(run_id, f"정합성 점검으로 실행을 닫았습니다 ({final})")
            repaired.append({"node_id": None, "did": f"끝난 실행을 {final} 로 닫았습니다"})

        run = await self.repos.runs.get(run_id)
        return {"ok": True, "run_id": run_id,
                "status": str(run.status) if run else "unknown", "repaired": repaired}

    async def reconcile_all(self, *, limit: int = 200, max_runs: int = 5000) -> dict[str, Any]:
        """Reconcile every run that is still supposed to be going.

        Paged through to the end rather than the newest `limit`: the runs most
        likely to be stuck are the oldest, and a single page sorted newest-first
        would be the one window that never contains them. One query per live
        run on top of that, which is fine while live runs number in the
        hundreds; `max_runs` is the backstop if they do not.
        """
        #  Collect first, repair after. Repairing while paging shrinks the set
        #  under the cursor and the runs that shift into the gap are skipped.
        ids: list[str] = []
        skip = 0
        while len(ids) < max_runs:
            page = await self.repos.runs.list(status=RunStatus.RUNNING, limit=limit, skip=skip)
            ids.extend(r.run_id for r in page)
            if len(page) < limit:
                break
            skip += limit
        repaired: list[dict[str, Any]] = []
        for run_id in ids:
            try:
                report = await self.reconcile(run_id)
            except Exception as exc:  # noqa: BLE001 - one bad run must not stop the sweep
                repaired.append({"run_id": run_id, "status": "error", "repaired": [], "error": str(exc)})
                continue
            if report.get("repaired"):
                repaired.append(report)
        return {"checked": len(ids), "repaired": repaired}

    # ------------------------------------------------------------ cancel

    async def cancel(self, run_id: str, *, reason: str = "사용자 취소") -> Run | None:
        """Cancel a run and drop whatever it left on the queue."""
        for job in await self.repos.jobs.list_for_run(run_id):
            if job.status in ("queued", "leased", "running"):
                await self.repos.jobs.finish(job.job_id, status="cancelled", error=reason)
        await self.repos.events.append(run_id, f"실행을 취소했습니다: {reason}", level="warning")
        return await self.repos.runs.set_status(run_id, RunStatus.CANCELLED, error=reason)
