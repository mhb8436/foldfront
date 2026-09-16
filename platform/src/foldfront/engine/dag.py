"""자유형 DAG 워크플로 엔진.

현행 RAPID 는 단계 순서가 코드에 고정되어 있다.

    msa -> rfd3 -> bioemu -> design -> soluprot -> af2 -> novelty

이 플랫폼은 여기에 더해 연구자가 노드를 자유롭게 조합하고 **병렬 분기 · 조건 분기 ·
사용자 정의 순서**를 설계할 수 있는 DAG 를 요구한다. 이 모듈은 그 그래프를 검증하고
실행 순서를 정한다.

설계 원칙 셋.

1. **위상 정렬로 실행 순서를 정한다.** 같은 계층(level)에 놓인 노드는 병렬로 돌린다.
2. **조건 분기는 실행 시점에 평가한다.** 거짓 가지에 달린 노드는 건너뛴다(skipped).
   건너뛴 노드만을 조상으로 갖는 노드도 함께 건너뛴다 — 그러지 않으면 입력 없이 실행된다.
3. **그래프 검증은 저장 시점에 한다.** 순환·고아 노드·없는 모델 참조를 실행 전에 잡는다.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Sequence

from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode


class GraphError(ValueError):
    """그래프가 실행 가능한 형태가 아니다."""


class NodeOutcome(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------- 그래프


@dataclass(frozen=True)
class Graph:
    """검증을 마친 실행 가능한 그래프."""

    nodes: dict[str, WorkflowNode]
    edges: tuple[WorkflowEdge, ...]
    outgoing: dict[str, tuple[WorkflowEdge, ...]]
    incoming: dict[str, tuple[WorkflowEdge, ...]]
    order: tuple[str, ...]          # 위상 정렬 결과
    levels: tuple[tuple[str, ...], ...]  # 같은 층은 병렬 실행 대상

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
    """워크플로를 검증하고 실행 가능한 그래프로 만든다.

    실행 전에 잡는 결함 — 노드 없음 · 중복 id · 없는 노드를 가리키는 간선 ·
    자기 자신으로 가는 간선 · 순환 · 분기 표시가 없는 조건 분기.
    """
    if not workflow.nodes:
        raise GraphError("노드가 없다")

    nodes: dict[str, WorkflowNode] = {}
    for n in workflow.nodes:
        if n.node_id in nodes:
            raise GraphError(f"노드 식별자가 중복된다: {n.node_id}")
        nodes[n.node_id] = n

    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)

    for e in workflow.edges:
        if e.source not in nodes:
            raise GraphError(f"간선의 출발 노드가 없다: {e.source}")
        if e.target not in nodes:
            raise GraphError(f"간선의 도착 노드가 없다: {e.target}")
        if e.source == e.target:
            raise GraphError(f"자기 자신으로 가는 간선이다: {e.source}")
        outgoing[e.source].append(e)
        incoming[e.target].append(e)

    #  조건 분기 노드는 나가는 간선에 참·거짓 표시가 있어야 한다
    for node_id, node in nodes.items():
        if node.kind is not NodeKind.BRANCH:
            continue
        outs = outgoing[node_id]
        if not outs:
            raise GraphError(f"조건 분기 노드에 나가는 간선이 없다: {node_id}")
        if any(e.branch is None for e in outs):
            raise GraphError(f"조건 분기 노드의 간선에 참·거짓 표시가 없다: {node_id}")
        if not node.condition:
            raise GraphError(f"조건 분기 노드에 조건식이 없다: {node_id}")

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
    """Kahn 알고리즘. 같은 층에 놓인 노드는 서로 의존하지 않으므로 병렬로 돌린다."""
    indeg = {n: len(incoming.get(n, [])) for n in nodes}
    frontier = deque(sorted(n for n, d in indeg.items() if d == 0))

    if not frontier:
        raise GraphError("시작 노드가 없다 — 순환이다")

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
        raise GraphError(f"순환이 있다: {', '.join(remaining)}")

    return tuple(order), tuple(levels)


# ---------------------------------------------------------------- 조건식


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """조건식을 평가한다.

    연구자가 화면에서 입력하는 값이므로 파이썬 eval 을 쓰지 않는다(취지).
    다음 형태만 받는다.

        <경로> <연산자> <수>      예) soluprot.pass_rate > 0.3
        <경로>                    예) af2.ok          (참·거짓으로 평가)

    경로는 점으로 중첩 사전을 훑는다. 값이 없으면 거짓이다.
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


# ---------------------------------------------------------------- 실행 계획


@dataclass
class NodeState:
    node_id: str
    outcome: NodeOutcome = NodeOutcome.PENDING
    reason: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """실행 상태를 들고 다음에 돌릴 노드를 내준다.

    엔진은 실제 실행을 하지 않는다 — 무엇을 돌릴지만 정한다. 실행은 작업 큐와
    워커가 담당한다. 그래야 같은 엔진으로 모의 실행과 실제 실행을 모두 돌릴 수 있다.
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

    # ------------------------------------------------------------ 조회

    def ready(self) -> tuple[str, ...]:
        """지금 실행할 수 있는 노드. 같은 층이면 병렬로 돌린다."""
        out: list[str] = []
        for node_id in self.graph.order:
            if self.states[node_id].outcome is not NodeOutcome.PENDING:
                continue
            if self._blocked(node_id):
                continue
            out.append(node_id)
        return tuple(out)

    def _blocked(self, node_id: str) -> bool:
        """선행 노드를 보고 지금 실행할 수 없는지 판정한다.

        합류 의미론이 두 갈래다.

        - **JOIN 노드는 OR 합류** — 부모 중 하나라도 살아 있으면 실행한다. 조건 분기 뒤의
          합류가 이것이라야 한다. AND 로 두면 한쪽 가지가 건너뛰어질 때 합류 노드가
          영원히 대기한다.
        - **그 밖의 노드는 AND 합류** — 부모가 모두 성공해야 실행한다.
        """
        edges = self.graph.incoming[node_id]
        if not edges:
            return False

        is_join = self.graph.nodes[node_id].kind is NodeKind.JOIN

        #  어느 쪽이든 아직 끝나지 않은 부모가 있으면 기다린다
        for edge in edges:
            if self.states[edge.source].outcome in (NodeOutcome.PENDING, NodeOutcome.RUNNING):
                return True

        if is_join:
            #  살아 있는 입력이 하나라도 있으면 실행한다
            return not any(self._edge_alive(edge) for edge in edges)

        return not all(self._edge_alive(edge) for edge in edges)

    def _edge_alive(self, edge: WorkflowEdge) -> bool:
        """이 간선으로 값이 흘러왔는지. 부모가 성공했고 분기 조건에 맞아야 한다."""
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

    # ------------------------------------------------------------ 갱신

    def mark_running(self, node_id: str) -> None:
        self.states[node_id].outcome = NodeOutcome.RUNNING

    def mark_succeeded(self, node_id: str, result: dict[str, Any] | None = None) -> None:
        state = self.states[node_id]
        state.outcome = NodeOutcome.SUCCEEDED
        state.result = dict(result or {})

        #  결과를 문맥에 쌓는다. 뒤따르는 조건식이 이 값을 본다
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

    # ------------------------------------------------------------ 건너뛰기 전파

    def _skip_untaken(self, branch_node: str, taken: str) -> None:
        for edge in self.graph.outgoing[branch_node]:
            if edge.branch is not None and edge.branch != taken:
                child = self.states[edge.target]
                if child.outcome is NodeOutcome.PENDING:
                    child.outcome = NodeOutcome.SKIPPED
                    child.reason = f"조건 분기에서 택하지 않은 가지({edge.branch})"

    def _cascade_skips(self) -> None:
        """입력을 받지 못하게 된 노드를 건너뛴다.

        `_blocked` 와 같은 합류 의미론을 쓴다 — JOIN 은 부모가 모두 죽었을 때,
        그 밖의 노드는 부모 중 하나라도 죽었을 때 건너뛴다. 둘이 어긋나면 노드가
        실행되지도 건너뛰어지지도 않은 채 남는다.
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

                #  아직 끝나지 않은 부모가 있으면 판정을 미룬다
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
                    state.reason = "선행 노드가 실행되지 않았다"
                    changed = True


# ---------------------------------------------------------------- 정형 체인


#  현행 RAPID 의 고정 단계. 자유형 DAG 로 표현해 두면 두 실행 방식이 한 엔진 위에서 돈다.
BUILTIN_STAGE_CHAIN: tuple[str, ...] = (
    "msa", "rfd3", "bioemu", "design", "soluprot", "af2", "novelty",
)


def builtin_pipeline_workflow(
    workflow_id: str = "builtin-pipeline", stages: Sequence[str] | None = None
) -> Workflow:
    """현행 고정 체인을 DAG 로 만든다(와 을 한 엔진으로 처리한다)."""
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
    """모델 노드가 Registry 에 없는 모델을 가리키는지 살핀다.

    저장은 막지 않고 경고만 낸다 — 모델을 먼저 등록하지 않고 워크플로를 설계하는 순서도 허용한다.
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
    """계획을 끝까지 돌린다. 시험과 모의 실행에 쓴다.

    실제 운영에서는 이 함수 대신 작업 큐에 노드를 넣고 워커가 처리한다.
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
