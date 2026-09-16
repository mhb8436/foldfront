"""Free-form DAG workflow engine.

The original fixes the stage order in code.

    msa -> rfd3 -> bioemu -> design -> soluprot -> af2 -> novelty

Researchers here compose nodes themselves, with parallel splits, conditional
branches and an order of their own. This module validates such a graph and
decides what runs when.

Three decisions shape it.

1. **A topological sort fixes the order.** Nodes that land on the same level
   have no dependency between them, so they run in parallel.
2. **Conditions are evaluated as the run proceeds.** Nodes on the branch not
   taken are skipped, and so is anything downstream whose only ancestors were
   skipped - otherwise it would run with no input.
3. **The graph is validated when it is saved,** not when it runs. A cycle, an
   orphan or a reference to a model that does not exist is worth catching
   before it costs GPU time.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Sequence

from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode


class GraphError(ValueError):
    """The graph is not in a runnable shape."""


class NodeOutcome(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------- graph


@dataclass(frozen=True)
class Graph:
    """A validated, runnable graph."""

    nodes: dict[str, WorkflowNode]
    edges: tuple[WorkflowEdge, ...]
    outgoing: dict[str, tuple[WorkflowEdge, ...]]
    incoming: dict[str, tuple[WorkflowEdge, ...]]
    order: tuple[str, ...]          # Topological order
    levels: tuple[tuple[str, ...], ...]  # One level runs in parallel

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(n for n in self.order if not self.incoming[n])

    @property
    def leaves(self) -> tuple[str, ...]:
        return tuple(n for n in self.order if not self.outgoing[n])

    def parents(self, node_id: str) -> tuple[str, ...]:
        return tuple(e.source for e in self.incoming[node_id])

    def children(self, node_id: str) -> tuple[str, ...]:
        return tuple(e.target for e in self.outgoing[node_id])


def build_graph(workflow: Workflow) -> Graph:
    """Validate a workflow and turn it into a runnable graph.

    Caught here rather than at run time: an empty graph, duplicate ids, an edge
    pointing at a node that does not exist, a self-edge, a cycle, and a
    conditional branch whose edges are not marked true or false.
    """
    if not workflow.nodes:
        raise GraphError("노드가 없습니다")

    nodes: dict[str, WorkflowNode] = {}
    for n in workflow.nodes:
        if n.node_id in nodes:
            raise GraphError(f"노드 식별자가 중복됩니다: {n.node_id}")
        nodes[n.node_id] = n

    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)

    for e in workflow.edges:
        if e.source not in nodes:
            raise GraphError(f"간선의 출발 노드가 없습니다: {e.source}")
        if e.target not in nodes:
            raise GraphError(f"간선의 도착 노드가 없습니다: {e.target}")
        if e.source == e.target:
            raise GraphError(f"자기 자신으로 가는 간선입니다: {e.source}")
        outgoing[e.source].append(e)
        incoming[e.target].append(e)

    #  A branch has to say which of its edges is the true one
    for node_id, node in nodes.items():
        if node.kind is not NodeKind.BRANCH:
            continue
        outs = outgoing[node_id]
        if not outs:
            raise GraphError(f"조건 분기 노드에 나가는 간선이 없습니다: {node_id}")
        if any(e.branch is None for e in outs):
            raise GraphError(f"조건 분기 노드의 간선에 참·거짓 표시가 없습니다: {node_id}")
        if not node.condition:
            raise GraphError(f"조건 분기 노드에 조건식이 없습니다: {node_id}")

    order, levels = _topological(nodes, incoming, outgoing)

    return Graph(
        nodes=nodes,
        edges=tuple(workflow.edges),
        outgoing={k: tuple(v) for k, v in _fill(outgoing, nodes)},
        incoming={k: tuple(v) for k, v in _fill(incoming, nodes)},
        order=order,
        levels=levels,
    )


def _fill(
    mapping: dict[str, list[WorkflowEdge]], nodes: dict[str, WorkflowNode]
) -> Iterable[tuple[str, list[WorkflowEdge]]]:
    for node_id in nodes:
        yield node_id, mapping.get(node_id, [])


def _topological(
    nodes: dict[str, WorkflowNode],
    incoming: dict[str, list[WorkflowEdge]],
    outgoing: dict[str, list[WorkflowEdge]],
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Kahn's algorithm. Nodes on one level do not depend on each other."""
    indeg = {n: len(incoming.get(n, [])) for n in nodes}
    frontier = deque(sorted(n for n, d in indeg.items() if d == 0))

    if not frontier:
        raise GraphError("시작 노드가 없습니다. 순환입니다")

    order: list[str] = []
    levels: list[tuple[str, ...]] = []

    while frontier:
        layer = tuple(sorted(frontier))
        levels.append(layer)
        frontier.clear()
        for node_id in layer:
            order.append(node_id)
            for e in outgoing.get(node_id, []):
                indeg[e.target] -= 1
                if indeg[e.target] == 0:
                    frontier.append(e.target)

    if len(order) != len(nodes):
        remaining = sorted(set(nodes) - set(order))
        raise GraphError(f"순환이 있습니다: {', '.join(remaining)}")

    return tuple(order), tuple(levels)


# ---------------------------------------------------------------- conditions


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a branch condition.

    The text arrives from a form a researcher fills in, so Python's eval is out
    of the question. Only two shapes are accepted:

        <path> <operator> <number>    e.g. soluprot.pass_rate > 0.3
        <path>                        e.g. af2.ok        (read as a boolean)

    A path walks nested dictionaries by dots. A value that is not there is
    false rather than an error - a condition on a stage that did not run should
    take the false branch, not stop the run.
    """
    expr = (expression or "").strip()
    if not expr:
        return False

    for op in (">=", "<=", "==", "!=", ">", "<"):
        if op in expr:
            left, right = expr.split(op, 1)
            value = _lookup(context, left.strip())
            other = _as_number(right.strip())
            if value is None or other is None:
                return False
            try:
                number = float(value)
            except (TypeError, ValueError):
                return False
            return {
                ">=": number >= other, "<=": number <= other,
                "==": number == other, "!=": number != other,
                ">": number > other, "<": number < other,
            }[op]

    return bool(_lookup(context, expr))


def _lookup(context: dict[str, Any], path: str) -> Any:
    cur: Any = context
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _as_number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------- execution plan


@dataclass
class NodeState:
    node_id: str
    outcome: NodeOutcome = NodeOutcome.PENDING
    reason: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Holds run state and hands out the nodes that are ready next.

    The engine decides what should run; it does not run anything. The queue and
    its workers do that. Keeping the two apart is what lets the same engine
    drive a mock run and a real one.
    """

    graph: Graph
    states: dict[str, NodeState]
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, graph: Graph, context: dict[str, Any] | None = None) -> "ExecutionPlan":
        return cls(
            graph=graph,
            states={n: NodeState(node_id=n) for n in graph.order},
            context=dict(context or {}),
        )

    # ------------------------------------------------------------ queries

    def ready(self) -> tuple[str, ...]:
        """Nodes that can start now. Those on one level go together."""
        out: list[str] = []
        for node_id in self.graph.order:
            if self.states[node_id].outcome is not NodeOutcome.PENDING:
                continue
            if self._blocked(node_id):
                continue
            out.append(node_id)
        return tuple(out)

    def _blocked(self, node_id: str) -> bool:
        """Decide whether a node must wait for its predecessors.

        Joining means two different things here.

        - **A JOIN node joins on OR.** One surviving parent is enough. A join
          after a conditional branch has to work this way; on AND it would wait
          forever as soon as one side was skipped.
        - **Every other node joins on AND.** All parents must have succeeded.
        """
        edges = self.graph.incoming[node_id]
        if not edges:
            return False

        is_join = self.graph.nodes[node_id].kind is NodeKind.JOIN

        #  Either way, a parent still running means wait
        for edge in edges:
            if self.states[edge.source].outcome in (NodeOutcome.PENDING, NodeOutcome.RUNNING):
                return True

        if is_join:
            #  One live input is enough to proceed
            return not any(self._edge_alive(edge) for edge in edges)

        return not all(self._edge_alive(edge) for edge in edges)

    def _edge_alive(self, edge: WorkflowEdge) -> bool:
        """Whether a value came down this edge: the parent succeeded and, on a branch, took this side."""
        parent = self.states[edge.source]
        if parent.outcome is not NodeOutcome.SUCCEEDED:
            return False
        if edge.branch is not None:
            taken = parent.result.get("_branch")
            if taken is not None and taken != edge.branch:
                return False
        return True

    def done(self) -> bool:
        return all(
            s.outcome not in (NodeOutcome.PENDING, NodeOutcome.RUNNING)
            for s in self.states.values()
        )

    def succeeded(self) -> bool:
        return self.done() and not any(
            s.outcome is NodeOutcome.FAILED for s in self.states.values()
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for s in self.states.values():
            counts[s.outcome] += 1
        return dict(counts)

    def context_for(self, node_id: str) -> dict[str, Any]:
        """What earlier stages produced, flattened for the node about to run.

        A builder asks for a value by name - `designed_fasta`, `backbone_pdb` -
        without knowing which stage produced it, so the results of every
        ancestor are merged in topological order. Later stages win, which is
        what a redesign loop needs: the second pass over a stage should be the
        one a downstream node sees.
        """
        ancestors: set[str] = set()
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for edge in self.graph.incoming.get(current, ()):
                if edge.source in ancestors:
                    continue
                ancestors.add(edge.source)
                frontier.append(edge.source)

        merged: dict[str, Any] = {}
        for name in self.graph.order:
            if name in ancestors and isinstance(self.context.get(name), dict):
                merged.update(self.context[name])
        return merged

    # ------------------------------------------------------------ updates

    def mark_running(self, node_id: str) -> None:
        self.states[node_id].outcome = NodeOutcome.RUNNING

    def mark_succeeded(self, node_id: str, result: dict[str, Any] | None = None) -> None:
        state = self.states[node_id]
        state.outcome = NodeOutcome.SUCCEEDED
        state.result = dict(result or {})

        #  Results accumulate into the context that later conditions read
        self.context[node_id] = state.result
        node = self.graph.nodes[node_id]
        if node.model_id:
            self.context.setdefault(node.model_id, state.result)

        if node.kind is NodeKind.BRANCH:
            taken = "true" if evaluate_condition(node.condition or "", self.context) else "false"
            state.result["_branch"] = taken
            self._skip_untaken(node_id, taken)

        self._cascade_skips()

    def mark_failed(self, node_id: str, reason: str | None = None) -> None:
        state = self.states[node_id]
        state.outcome = NodeOutcome.FAILED
        state.reason = reason
        self._cascade_skips()

    def mark_skipped(self, node_id: str, reason: str | None = None) -> None:
        state = self.states[node_id]
        state.outcome = NodeOutcome.SKIPPED
        state.reason = reason
        self._cascade_skips()

    # ------------------------------------------------------------ skip propagation

    def _skip_untaken(self, branch_node: str, taken: str) -> None:
        for edge in self.graph.outgoing[branch_node]:
            if edge.branch is not None and edge.branch != taken:
                child = self.states[edge.target]
                if child.outcome is NodeOutcome.PENDING:
                    child.outcome = NodeOutcome.SKIPPED
                    child.reason = f"조건 분기에서 택하지 않은 가지입니다({edge.branch})"

    def _cascade_skips(self) -> None:
        """Skip nodes that can no longer receive input.

        This must use the same join semantics as `_blocked`: a JOIN is skipped
        only once every parent is gone, anything else as soon as one parent is.
        When the two disagree a node ends up neither run nor skipped, and the
        run stalls with no error to show for it.
        """
        changed = True
        while changed:
            changed = False
            for node_id in self.graph.order:
                state = self.states[node_id]
                if state.outcome is not NodeOutcome.PENDING:
                    continue
                edges = self.graph.incoming[node_id]
                if not edges:
                    continue

                #  A parent still running means the decision can wait
                if any(
                    self.states[e.source].outcome in (NodeOutcome.PENDING, NodeOutcome.RUNNING)
                    for e in edges
                ):
                    continue

                is_join = self.graph.nodes[node_id].kind is NodeKind.JOIN
                alive = [e for e in edges if self._edge_alive(e)]

                dead_out = not alive if is_join else len(alive) != len(edges)
                if dead_out:
                    state.outcome = NodeOutcome.SKIPPED
                    state.reason = "선행 노드가 실행되지 않았습니다"
                    changed = True


# ---------------------------------------------------------------- fixed chain


#  The original's fixed stages. Expressed as a DAG, both ways of running a
#  pipeline sit on one engine instead of two.
BUILTIN_STAGE_CHAIN: tuple[str, ...] = (
    "msa", "rfd3", "bioemu", "design", "soluprot", "af2", "novelty",
)


def builtin_pipeline_workflow(
    workflow_id: str = "builtin-pipeline", stages: Sequence[str] | None = None
) -> Workflow:
    """Express the original fixed chain as a DAG."""
    names = tuple(stages or BUILTIN_STAGE_CHAIN)
    nodes = [
        WorkflowNode(
            node_id=name, kind=NodeKind.MODEL, model_id=name, label=name,
            position={"x": float(i * 180), "y": 0.0},
        )
        for i, name in enumerate(names)
    ]
    edges = [
        WorkflowEdge(source=a, target=b) for a, b in zip(names, names[1:])
    ]
    return Workflow(
        workflow_id=workflow_id,
        name="정형 단계 실행",
        description="현행 RAPID v1.0.29 의 고정 단계 체인을 DAG 로 표현한 기본 템플릿",
        nodes=nodes,
        edges=edges,
        is_template=True,
        is_builtin=True,
    )


def validate_model_refs(
    workflow: Workflow, known_model_ids: Iterable[str]
) -> list[str]:
    """Report model nodes that name something the registry does not have.

    This warns rather than refuses. Designing a workflow before registering the
    models it will use is a reasonable order to work in.
    """
    known = set(known_model_ids)
    missing: list[str] = []
    for node in workflow.nodes:
        if node.kind is NodeKind.MODEL and node.model_id and node.model_id not in known:
            missing.append(node.model_id)
    return sorted(set(missing))


def run_plan(
    graph: Graph,
    executor: Callable[[WorkflowNode], dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
) -> ExecutionPlan:
    """Drive a plan to completion. For tests and mock runs.

    In production nothing calls this: nodes go on the queue and workers take
    them, which is what makes the run survive a worker dying.
    """
    plan = ExecutionPlan.start(graph, context)
    while True:
        ready = plan.ready()
        if not ready:
            break
        for node_id in ready:
            plan.mark_running(node_id)
            try:
                result = executor(graph.nodes[node_id])
            except Exception as exc:
                plan.mark_failed(node_id, str(exc))
            else:
                plan.mark_succeeded(node_id, result)
    return plan
