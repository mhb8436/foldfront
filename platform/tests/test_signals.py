"""품질 신호 시험.

판정 기준은 원본 `agent_panel.py` 의 것이다. 여기서 보는 것은 그 기준이
우리 지표 이름 위에서 제대로 걸리는지, 그리고 **없는 값으로 경고를 지어내지
않는지**다.
"""

from __future__ import annotations

from foldfront.db.models import Run, RunStatus, StageState
from foldfront.engine.signals import (
    AF2_PLDDT_LOW,
    MSA_DEPTH_LOW,
    SOLUPROT_RATE_LOW,
    read_signals,
)


def _run(*stages: StageState) -> Run:
    return Run(run_id="run-s", status=RunStatus.SUCCEEDED, stages=list(stages))


def _ok(name: str, model: str, **metrics) -> StageState:
    return StageState(name=name, status=RunStatus.SUCCEEDED,
                      model_id=model, metrics=metrics)


def test_얕은_정렬을_경고한다():
    signals = read_signals(_run(_ok("msa", "msa", depth=12)))

    assert len(signals) == 1
    assert signals[0].level == "warning"
    assert signals[0].evidence["threshold"] == MSA_DEPTH_LOW
    assert signals[0].source == "agent_panel._interpret_msa"
    assert signals[0].advice


def test_깊은_정렬은_아무_말도_하지_않는다():
    assert read_signals(_run(_ok("msa", "msa", depth=800, coverage=0.9))) == []


def test_통과_서열이_없으면_오류로_본다():
    signals = read_signals(_run(_ok("soluprot", "soluprot", scored=40, passed=0)))

    assert [s.level for s in signals] == ["error"]
    assert "soluprot_cutoff" in signals[0].advice


def test_통과율이_낮으면_경고한다():
    signals = read_signals(_run(
        _ok("soluprot", "soluprot", scored=40, passed=4, pass_rate=0.1),
    ))

    assert [s.level for s in signals] == ["warning"]
    assert signals[0].evidence["threshold"] == SOLUPROT_RATE_LOW


def test_pLDDT_가_낮으면_경고한다():
    signals = read_signals(_run(_ok("af2", "af2", plddt=61.2)))

    assert [s.level for s in signals] == ["warning"]
    assert signals[0].evidence["threshold"] == AF2_PLDDT_LOW


def test_없는_지표로_경고를_지어내지_않는다():
    """측정하지 않은 값에서 나온 경고는 없느니만 못하다."""
    assert read_signals(_run(_ok("af2", "af2", rmsd=1.2))) == []
    assert read_signals(_run(_ok("soluprot", "soluprot"))) == []


def test_모의_어댑터_값은_판정하지_않는다():
    """모의 수치를 근거처럼 읽으면 시연이 주장이 된다."""
    signals = read_signals(_run(
        _ok("af2", "af2", plddt=40.0, _mock=True),
    ))

    assert [s.level for s in signals] == ["info"]
    assert "모의" in signals[0].message
    assert signals[0].evidence["_mock"] is True


def test_실패한_단계는_그것만으로_오류다():
    run = _run(StageState(name="af2", status=RunStatus.FAILED,
                          model_id="af2", error="GPU 없음"))

    signals = read_signals(run)

    assert [s.level for s in signals] == ["error"]
    assert signals[0].advice == "GPU 없음"


def test_아직_돌지_않은_단계는_건너뛴다():
    run = _run(StageState(name="af2", status=RunStatus.PENDING, model_id="af2"))

    assert read_signals(run) == []


def test_모르는_모델은_판정하지_않는다():
    """우리가 기준을 모르는 모델에 대해 말을 얹지 않는다."""
    assert read_signals(_run(_ok("x", "사용자모델", 점수=1))) == []


def test_여러_단계의_신호가_단계_순서대로_나온다():
    signals = read_signals(_run(
        _ok("msa", "msa", depth=10),
        _ok("soluprot", "soluprot", scored=10, passed=0),
        _ok("af2", "af2", plddt=50.0),
    ))

    assert [s.stage for s in signals] == ["msa", "soluprot", "af2"]
    assert [s.level for s in signals] == ["warning", "error", "warning"]


def test_노드_이름이_아니라_모델로_판정한다():
    """연구자는 노드 이름을 마음대로 짓는다. 판정은 값을 낸 모델의 것이다."""
    signals = read_signals(_run(_ok("1차정렬", "msa", depth=3)))

    assert [s.stage for s in signals] == ["1차정렬"]
    assert signals[0].source == "agent_panel._interpret_msa"


def test_보고된_pLDDT_와_파일이_어긋나면_중대로_본다():
    """아래 화면이 전부 보고된 값을 그린다. 여기서 잡지 않으면 아무도 안 잡는다."""
    signals = read_signals(_run(_ok(
        "af2", "af2", plddt=92.0, plddt_reported=92.0,
        plddt_measured=40.0, plddt_gap=52.0, plddt_agrees=False,
    )))

    assert signals[0].level == "error"
    assert "근거로 쓰지 마십시오" in signals[0].advice
    assert signals[0].evidence["plddt_measured"] == 40.0


def test_일치하면_그것으로_경고하지_않는다():
    signals = read_signals(_run(_ok(
        "af2", "af2", plddt=88.0, plddt_reported=88.0,
        plddt_measured=88.0, plddt_gap=0.0, plddt_agrees=True,
    )))

    assert signals == []


def test_보존_위치가_하나도_없으면_경고한다():
    signals = read_signals(_run(_ok(
        "msa", "msa", depth=400, coverage=0.9, query_length=100,
        fixed_positions={"0.1": 0, "0.3": 0},
    )))

    assert [s.level for s in signals] == ["warning"]
    assert "보존 위치" in signals[0].message


def test_거의_전부_고정되면_설계_여지가_없다고_한다():
    signals = read_signals(_run(_ok(
        "msa", "msa", depth=400, coverage=0.9, query_length=100,
        fixed_positions={"0.9": 92},
    )))

    assert [s.level for s in signals] == ["warning"]
    assert "설계 여지" in signals[0].message
