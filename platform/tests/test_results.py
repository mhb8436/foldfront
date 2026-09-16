"""Turning a model's reply into metrics.

What matters is that a branch condition can answer from it, and that a reply
which does not fit does not fail a stage that already ran.
"""

from __future__ import annotations

from foldfront.engine.dag import evaluate_condition
from foldfront.engine.results import SOLUBILITY_THRESHOLD, interpret, known_models


def _reply(*scores: float) -> dict:
    return {"results": [{"id": f"d{i}", "score": s} for i, s in enumerate(scores, 1)]}


def test_점수_목록이_통과율이_된다():
    m = interpret("soluprot", _reply(0.9, 0.2, 0.7))

    assert m["scored"] == 3
    assert m["passed"] == 2
    assert m["pass_rate"] == 0.667


def test_통과한_것이_무엇인지_남긴다():
    """The next stage acts on the survivors, so naming them is the point."""
    m = interpret("soluprot", _reply(0.9, 0.2, 0.7))

    assert m["passed_ids"] == ["d1", "d3"]


def test_경계값은_통과로_친다():
    assert interpret("soluprot", _reply(SOLUBILITY_THRESHOLD))["passed"] == 1


def test_분기_조건이_이_값을_읽는다():
    """This is what the whole interpretation exists for."""
    #  Two of three pass, so the rate is 0.667 - above the template threshold
    #  and below one that would demand nearly everything survive.
    m = interpret("soluprot", _reply(0.9, 0.2, 0.7))

    assert evaluate_condition("soluprot.pass_rate > 0.3", {"soluprot": m}) is True
    assert evaluate_condition("soluprot.pass_rate > 0.9", {"soluprot": m}) is False


def test_빈_응답도_분기가_읽을_수_있게_낸다():
    """A stage that scored nothing must still answer a condition, as false."""
    m = interpret("soluprot", {"results": []})

    assert m["pass_rate"] == 0.0
    assert evaluate_condition("soluprot.pass_rate > 0.3", {"soluprot": m}) is False


def test_원본_응답을_버리지_않는다():
    """A later stage may need a field no metric covers."""
    m = interpret("soluprot", _reply(0.9))

    assert "results" in m


def test_해석기가_없는_모델은_그대로_넘긴다():
    reply = {"whatever": 1}

    assert interpret("우리모델", reply) == reply


def test_모양이_다른_응답은_단계를_실패시키지_않는다():
    """The model ran. Failing it over a shape we did not expect would lose that."""
    assert interpret("soluprot", {"results": "목록이 아니다"})["results"] == "목록이 아니다"
    assert interpret("rfd3", {"backbones": None}) == {"backbones": None}


def test_승계한_모델에_해석기가_있다():
    assert {"soluprot", "rfd3", "bioemu", "proteinmpnn"} <= set(known_models())
