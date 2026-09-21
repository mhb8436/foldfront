"""계획 생성 시험.

문장을 읽어 조건을 뽑는 일은 원본 `router.plan_from_prompt` 가 한다. 여기서
보는 것은 그 답을 **워크플로 초안으로 옮기는 부분**이고, 무엇보다 초안이
실행이 아니라는 것이다.
"""

from __future__ import annotations

import pytest

from foldfront.engine.planner import plan

pytest.importorskip("pipeline_mcp.router")


def test_문장이_비면_거절한다():
    with pytest.raises(ValueError):
        plan("   ")


def test_조건이_없으면_정형_체인을_내고_그렇다고_말한다():
    """기본 체인을 라우터가 내린 판단처럼 보이게 하지 않는다."""
    drafted = plan("단백질을 설계해 주십시오", target_fasta="MKT")

    assert drafted["stages"][0] == "msa"
    assert drafted["defaulted"] is True
    assert "모델 이름" in drafted["note"]


def test_초안은_단계를_노드로_잇는다():
    drafted = plan("설계해 주십시오", target_fasta="MKT")

    wf = drafted["workflow"]
    names = [n["node_id"] for n in wf["nodes"]]
    assert names == drafted["stages"]
    assert [(e["source"], e["target"]) for e in wf["edges"]] == list(zip(names, names[1:]))
    assert all(n["kind"] == "model" for n in wf["nodes"])


def test_초안은_저장하지도_실행하지도_않는다():
    """초안은 초안이다. 식별자가 없으므로 저장 전에는 어디에도 없다."""
    drafted = plan("설계해 주십시오", target_fasta="MKT")

    assert drafted["workflow"]["workflow_id"] == ""
    assert drafted["workflow"]["is_template"] is False


def test_필요한_입력이_없으면_준비되지_않았다고_한다():
    """기본값으로 채워 시작하면 추측에 돈을 쓰게 된다."""
    drafted = plan("rfd3 로 백본을 만들어 주십시오")

    assert drafted["ready"] is False
    assert "rfd3_input_pdb" in drafted["missing"]
    assert any(q["id"] == "rfd3_input_pdb" for q in drafted["questions"])
    assert "실행하지 않습니다" in drafted["note"]


def test_입력을_주면_준비됐다고_한다():
    drafted = plan("rfd3 로 백본을 만들어 주십시오",
                   target_fasta="MKT", rfd3_input_pdb="ATOM ...")

    assert drafted["ready"] is True
    assert drafted["missing"] == []


def test_멈출_지점을_읽으면_체인을_거기서_끊는다():
    drafted = plan("soluprot 까지만 돌려 주십시오 stop_after=soluprot",
                   target_fasta="MKT")

    assert drafted["stages"][-1] == "soluprot"
    assert "af2" not in drafted["stages"]


def test_원본이_쓰는_wt_diff_라는_이름을_받아들인다():
    """원본 질문은 wt_diff 를 제시하고 기본값은 novelty 다. 같은 단계다."""
    drafted = plan("stop_after=wt_diff 로 해 주십시오", target_fasta="MKT")

    assert drafted["stages"][-1] == "novelty"


def test_라우터가_읽은_조건을_해당_단계의_설정으로_옮긴다():
    drafted = plan("num_seq_per_tier=8 로 설계해 주십시오", target_fasta="MKT")

    design = next(n for n in drafted["workflow"]["nodes"] if n["node_id"] == "design")
    assert design["params"]["num_seq_per_tier"] == 8
    #  단계에 속하지 않는 값은 노드에 얹지 않는다
    msa = next(n for n in drafted["workflow"]["nodes"] if n["node_id"] == "msa")
    assert "num_seq_per_tier" not in msa["params"]


def test_원본의_답을_손대지_않고_함께_낸다():
    """라우터가 낸 것과 우리가 만든 것을 섞지 않는다."""
    drafted = plan("rfd3 로 만들어 주십시오")

    assert set(drafted) >= {"routed_request", "missing", "questions", "errors",
                            "stages", "workflow", "ready"}


def test_조건_분기를_지어내지_않는다():
    """라우터에 조건이라는 개념이 없다. 여기서 만들면 아무도 요청하지 않은 규칙이 된다."""
    drafted = plan("용해도가 낮으면 건너뛰어 주십시오", target_fasta="MKT")

    assert all(n["kind"] == "model" for n in drafted["workflow"]["nodes"])
    assert all(n["condition"] is None for n in drafted["workflow"]["nodes"])
