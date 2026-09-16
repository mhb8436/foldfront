"""DAG 엔진 시험.

MongoDB 없이 돈다 — 엔진은 순수 계산이다.
"""

from __future__ import annotations

import pytest

from foldfront.db.models import NodeKind, Workflow, WorkflowEdge, WorkflowNode
from foldfront.engine.dag import (
    BUILTIN_STAGE_CHAIN,
    ExecutionPlan,
    GraphError,
    NodeOutcome,
    build_graph,
    builtin_pipeline_workflow,
    evaluate_condition,
    run_plan,
    validate_model_refs,
)


def wf(nodes, edges, **kw) -> Workflow:
    return Workflow(workflow_id="t", name="시험", nodes=nodes, edges=edges, **kw)


def model(node_id: str, model_id: str | None = None) -> WorkflowNode:
    return WorkflowNode(node_id=node_id, kind=NodeKind.MODEL, model_id=model_id or node_id)


def edge(a: str, b: str, branch: str | None = None) -> WorkflowEdge:
    return WorkflowEdge(source=a, target=b, branch=branch)


# ---------------------------------------------------------------- 그래프 검증


def test_직선_체인의_실행_순서를_정한다():
    g = build_graph(wf([model("a"), model("b"), model("c")], [edge("a", "b"), edge("b", "c")]))

    assert g.order == ("a", "b", "c")
    assert g.levels == (("a",), ("b",), ("c",))
    assert g.roots == ("a",)
    assert g.leaves == ("c",)


def test_병렬_분기는_같은_층에_놓인다():
    """같은 층은 동시에 돌린다."""
    g = build_graph(wf(
        [model("a"), model("b"), model("c"), model("d")],
        [edge("a", "b"), edge("a", "c"), edge("b", "d"), edge("c", "d")],
    ))

    assert g.levels == (("a",), ("b", "c"), ("d",))
    assert g.parents("d") == ("b", "c")


def test_순환을_잡는다():
    with pytest.raises(GraphError, match="순환"):
        build_graph(wf([model("a"), model("b")], [edge("a", "b"), edge("b", "a")]))


def test_자기_자신으로_가는_간선을_잡는다():
    with pytest.raises(GraphError, match="자기 자신"):
        build_graph(wf([model("a")], [edge("a", "a")]))


def test_없는_노드를_가리키는_간선을_잡는다():
    with pytest.raises(GraphError, match="도착 노드"):
        build_graph(wf([model("a")], [edge("a", "zzz")]))


def test_중복된_노드_식별자를_잡는다():
    with pytest.raises(GraphError, match="중복"):
        build_graph(wf([model("a"), model("a")], []))


def test_노드가_없으면_거부한다():
    with pytest.raises(GraphError, match="노드가 없"):
        build_graph(wf([], []))


def test_조건분기_노드는_조건식과_분기표시가_있어야_한다():
    branch = WorkflowNode(node_id="b", kind=NodeKind.BRANCH, condition="x > 1")

    #  분기 표시가 없는 간선
    with pytest.raises(GraphError, match="참·거짓 표시"):
        build_graph(wf([branch, model("c")], [edge("b", "c")]))

    #  조건식이 없는 분기 노드
    no_cond = WorkflowNode(node_id="b", kind=NodeKind.BRANCH)
    with pytest.raises(GraphError, match="조건식"):
        build_graph(wf([no_cond, model("c")], [edge("b", "c", branch="true")]))


def test_연결되지_않은_노드도_실행_대상이다():
    """고아 노드는 결함이 아니다 — 독립 분기를 병렬로 돌리는 설계가 가능하다."""
    g = build_graph(wf([model("a"), model("b")], []))

    assert set(g.roots) == {"a", "b"}
    assert g.levels == (("a", "b"),)


# ---------------------------------------------------------------- 조건식


@pytest.mark.parametrize(
    "expr,ctx,expected",
    [
        ("soluprot.pass_rate > 0.3", {"soluprot": {"pass_rate": 0.5}}, True),
        ("soluprot.pass_rate > 0.3", {"soluprot": {"pass_rate": 0.1}}, False),
        ("af2.plddt >= 80", {"af2": {"plddt": 80}}, True),
        ("af2.plddt < 80", {"af2": {"plddt": 80}}, False),
        ("design.count == 5", {"design": {"count": 5}}, True),
        ("design.count != 5", {"design": {"count": 5}}, False),
        ("af2.ok", {"af2": {"ok": True}}, True),
        ("af2.ok", {"af2": {"ok": False}}, False),
        #  값이 없으면 거짓이다 — 예외를 던지지 않는다
        ("없는.경로 > 1", {}, False),
        ("", {}, False),
        #  문자열은 수 비교에서 거짓
        ("a.b > 1", {"a": {"b": "문자열"}}, False),
    ],
)
def test_조건식을_평가한다(expr, ctx, expected):
    assert evaluate_condition(expr, ctx) is expected


def test_조건식은_파이썬_코드를_실행하지_않는다():
    """취지 — 화면에서 들어온 문자열을 eval 하지 않는다."""
    ctx = {"x": {"y": 1}}

    assert evaluate_condition("__import__('os').system('echo x')", ctx) is False
    assert evaluate_condition("1 if True else 0", ctx) is False


# ---------------------------------------------------------------- 실행 계획


def test_병렬_노드가_함께_준비된다():
    g = build_graph(wf(
        [model("a"), model("b"), model("c")], [edge("a", "b"), edge("a", "c")],
    ))
    plan = ExecutionPlan.start(g)

    assert plan.ready() == ("a",)

    plan.mark_running("a")
    assert plan.ready() == ()          # 실행 중인 노드는 다시 내주지 않는다

    plan.mark_succeeded("a", {})
    assert plan.ready() == ("b", "c")  # 둘을 동시에 돌린다


def test_전체를_돌리면_모든_노드가_성공한다():
    g = build_graph(builtin_pipeline_workflow())
    called: list[str] = []

    plan = run_plan(g, lambda node: called.append(node.node_id) or {"ok": True})

    assert called == list(BUILTIN_STAGE_CHAIN)
    assert plan.succeeded()
    assert plan.summary()[NodeOutcome.SUCCEEDED] == len(BUILTIN_STAGE_CHAIN)


def test_조건분기는_택하지_않은_가지를_건너뛴다():
    """조건 분기."""
    nodes = [
        model("design"),
        WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="design.pass_rate > 0.3"),
        model("af2"),
        model("stop"),
    ]
    edges = [
        edge("design", "gate"),
        edge("gate", "af2", branch="true"),
        edge("gate", "stop", branch="false"),
    ]
    g = build_graph(wf(nodes, edges))

    def run(node):
        return {"pass_rate": 0.5} if node.node_id == "design" else {"ok": True}

    plan = run_plan(g, run)

    assert plan.states["af2"].outcome is NodeOutcome.SUCCEEDED
    assert plan.states["stop"].outcome is NodeOutcome.SKIPPED
    assert plan.states["gate"].result["_branch"] == "true"
    assert plan.succeeded()


def test_조건이_거짓이면_반대_가지가_돈다():
    nodes = [
        model("design"),
        WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="design.pass_rate > 0.3"),
        model("af2"),
        model("stop"),
    ]
    edges = [
        edge("design", "gate"),
        edge("gate", "af2", branch="true"),
        edge("gate", "stop", branch="false"),
    ]
    g = build_graph(wf(nodes, edges))

    plan = run_plan(
        g, lambda node: {"pass_rate": 0.1} if node.node_id == "design" else {"ok": True}
    )

    assert plan.states["af2"].outcome is NodeOutcome.SKIPPED
    assert plan.states["stop"].outcome is NodeOutcome.SUCCEEDED


def test_건너뛴_노드의_후손도_건너뛴다():
    """이것이 없으면 입력 없는 노드가 실행 대상으로 올라온다."""
    nodes = [
        WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="gate.ok"),
        model("a"), model("b"), model("c"),
    ]
    edges = [
        edge("gate", "a", branch="true"),
        edge("gate", "b", branch="false"),
        edge("b", "c"),
    ]
    g = build_graph(wf(nodes, edges))

    plan = run_plan(g, lambda node: {"ok": True})

    assert plan.states["b"].outcome is NodeOutcome.SKIPPED
    assert plan.states["c"].outcome is NodeOutcome.SKIPPED
    assert "선행 노드" in (plan.states["c"].reason or "")


def test_실패한_노드의_후손은_건너뛴다():
    """실행 중 오류가 나도 계획이 멈추지 않고 정리된다."""
    g = build_graph(wf(
        [model("a"), model("b"), model("c")], [edge("a", "b"), edge("b", "c")],
    ))

    def run(node):
        if node.node_id == "b":
            raise RuntimeError("GPU 없음")
        return {"ok": True}

    plan = run_plan(g, run)

    assert plan.states["a"].outcome is NodeOutcome.SUCCEEDED
    assert plan.states["b"].outcome is NodeOutcome.FAILED
    assert plan.states["b"].reason == "GPU 없음"
    assert plan.states["c"].outcome is NodeOutcome.SKIPPED
    assert plan.done()
    assert not plan.succeeded()


def test_한_갈래가_실패해도_다른_갈래는_돈다():
    g = build_graph(wf(
        [model("root"), model("left"), model("right"), model("leaf")],
        [edge("root", "left"), edge("root", "right"), edge("left", "leaf")],
    ))

    def run(node):
        if node.node_id == "left":
            raise RuntimeError("실패")
        return {"ok": True}

    plan = run_plan(g, run)

    assert plan.states["right"].outcome is NodeOutcome.SUCCEEDED
    assert plan.states["leaf"].outcome is NodeOutcome.SKIPPED


def test_한_부모만_살아_있으면_자식은_실행된다():
    """합류 노드는 모든 부모가 죽었을 때만 건너뛴다."""
    nodes = [
        WorkflowNode(node_id="g1", kind=NodeKind.BRANCH, condition="g1.ok"),
        model("a"), model("b"), WorkflowNode(node_id="join", kind=NodeKind.JOIN),
    ]
    edges = [
        edge("g1", "a", branch="true"),
        edge("g1", "b", branch="false"),
        edge("a", "join"),
        edge("b", "join"),
    ]
    g = build_graph(wf(nodes, edges))

    plan = run_plan(g, lambda node: {"ok": True})

    assert plan.states["a"].outcome is NodeOutcome.SUCCEEDED
    assert plan.states["b"].outcome is NodeOutcome.SKIPPED
    #  a 가 살아 있으므로 합류 노드는 돈다
    assert plan.states["join"].outcome is NodeOutcome.SUCCEEDED


def test_노드_결과가_문맥에_쌓여_뒤_조건식이_본다():
    nodes = [
        model("soluprot"),
        WorkflowNode(node_id="gate", kind=NodeKind.BRANCH, condition="soluprot.pass_rate > 0.4"),
        model("af2"), model("stop"),
    ]
    edges = [
        edge("soluprot", "gate"),
        edge("gate", "af2", branch="true"),
        edge("gate", "stop", branch="false"),
    ]
    g = build_graph(wf(nodes, edges))

    plan = run_plan(
        g, lambda n: {"pass_rate": 0.9} if n.node_id == "soluprot" else {}
    )

    #  model_id 로도 참조할 수 있다
    assert plan.context["soluprot"]["pass_rate"] == 0.9
    assert plan.states["af2"].outcome is NodeOutcome.SUCCEEDED


# ---------------------------------------------------------------- 정형 체인


def test_현행_고정체인을_DAG_로_표현한다():
    """고정 단계 실행과 자유형 DAG 를 한 엔진으로 처리한다."""
    w = builtin_pipeline_workflow()
    g = build_graph(w)

    assert g.order == BUILTIN_STAGE_CHAIN
    assert w.is_builtin is True
    assert w.is_template is True
    #  화면 배치 좌표가 들어 있다
    assert w.nodes[1].position["x"] == 180.0


def test_일부_단계만_고른_체인도_만든다():
    w = builtin_pipeline_workflow(stages=["msa", "design", "af2"])

    assert build_graph(w).order == ("msa", "design", "af2")


def test_없는_모델_참조를_경고로_알린다():
    w = wf([model("a", "rfd3"), model("b", "없는모델"), model("c", "af2")], [])

    missing = validate_model_refs(w, known_model_ids=["rfd3", "af2"])

    assert missing == ["없는모델"]


def test_JOIN_은_모든_부모가_죽어야_건너뛴다():
    """회귀 시험 — 합류 노드가 PENDING 으로 영구 정체되던 결함.

    `_blocked` 는 부모 하나만 건너뛰어도 막는데 `_cascade_skips` 는 모든 부모가 죽어야
    건너뛰었다. 둘이 어긋나 조건 분기 뒤의 합류 노드가 실행되지도 건너뛰어지지도 않았다.
    """
    nodes = [
        WorkflowNode(node_id="g", kind=NodeKind.BRANCH, condition="g.ok"),
        model("t"), model("f"), WorkflowNode(node_id="join", kind=NodeKind.JOIN),
    ]
    edges = [
        edge("g", "t", branch="true"),
        edge("g", "f", branch="false"),
        edge("t", "join"),
        edge("f", "join"),
    ]
    g = build_graph(wf(nodes, edges))

    plan = run_plan(g, lambda n: {"ok": True})

    assert plan.done()                       # 정체되지 않는다
    assert plan.states["join"].outcome is NodeOutcome.SUCCEEDED


def test_JOIN_도_부모가_전부_죽으면_건너뛴다():
    nodes = [
        model("a"), WorkflowNode(node_id="join", kind=NodeKind.JOIN), model("tail"),
    ]
    edges = [edge("a", "join"), edge("join", "tail")]
    g = build_graph(wf(nodes, edges))

    def run(node):
        if node.node_id == "a":
            raise RuntimeError("실패")
        return {}

    plan = run_plan(g, run)

    assert plan.states["join"].outcome is NodeOutcome.SKIPPED
    assert plan.states["tail"].outcome is NodeOutcome.SKIPPED
    assert plan.done()


def test_일반_노드는_부모_하나만_죽어도_건너뛴다():
    """AND 합류 — JOIN 이 아닌 노드는 입력이 모두 있어야 실행된다."""
    g = build_graph(wf(
        [model("a"), model("b"), model("c")],
        [edge("a", "c"), edge("b", "c")],
    ))

    def run(node):
        if node.node_id == "b":
            raise RuntimeError("실패")
        return {}

    plan = run_plan(g, run)

    assert plan.states["a"].outcome is NodeOutcome.SUCCEEDED
    assert plan.states["c"].outcome is NodeOutcome.SKIPPED
    assert plan.done()
