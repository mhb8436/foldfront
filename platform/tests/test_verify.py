"""모델이 낸 값을 그 모델이 낸 파일로 다시 확인한다.

계산은 원본의 것이다 — `bio/a3m.py` 의 보존도·MSA 품질, `bio/pdb.py` 의
Kabsch 중첩. 여기서 보는 것은 그것을 **불러다 쓰는지**, 그리고 확인할 수
없을 때 확인한 척하지 않는지다.
"""

from __future__ import annotations

import pytest

from foldfront.engine.results import interpret
from foldfront.engine.signals import PLDDT_TOLERANCE
from foldfront.engine.verify import check_structure, mean_plddt, read_msa

pytest.importorskip("pipeline_mcp.bio.a3m")


#  같은 길이 3개. 1번 위치는 전부 M, 마지막은 제각각이다.
A3M = """>query
MKTAYIAK
>hit1
MKTAYIAR
>hit2
MKTAYIAQ
>hit3
MKTAYIAW
"""


def _pdb(*plddt: float) -> str:
    """CA 원자만 있는 최소 PDB. B-factor 자리에 pLDDT 가 들어간다."""
    lines = []
    for i, b in enumerate(plddt, start=1):
        lines.append(
            f"ATOM  {i:5d}  CA  ALA A{i:4d}    "
            f"{i * 3.8:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{b:6.2f}           C"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- MSA


def test_정렬에서_깊이와_적용범위를_실제로_센다():
    out = read_msa(A3M)

    assert out["usable_hits"] == 3
    assert out["depth"] == 3
    assert 0.0 < out["coverage"] <= 1.0


def test_보존도_tier_로_고정할_위치를_고른다():
    """SFR-003 — 지금까지 tier 는 아무도 적용하지 않는 파라미터였다."""
    out = read_msa(A3M, tiers=[0.25, 0.5])

    assert out["query_length"] == 8
    #  8 자리의 25% = 2 자리, 50% = 4 자리
    assert out["fixed_positions"]["0.25"] == 2
    assert out["fixed_positions"]["0.5"] == 4
    #  설계 단계가 지켜야 하므로 위치 자체도 함께 낸다
    assert len(out["fixed_positions_by_tier"]["0.25"]) == 2


def test_tier_를_주지_않으면_고정_위치를_계산하지_않는다():
    """어디를 고정할지는 설계자의 결정이다. 기본값을 정하지 않는다."""
    out = read_msa(A3M)

    assert "fixed_positions" not in out


def test_빈_정렬에는_아무_말도_하지_않는다():
    assert read_msa("") == {}
    assert read_msa("   ") == {}


def test_읽을_수_없는_정렬은_그렇다고_적는다():
    out = read_msa("이건 a3m 이 아닙니다")

    assert "msa_unreadable" in out


# ---------------------------------------------------------------- 구조


def test_구조_파일에서_pLDDT_를_직접_잰다():
    assert mean_plddt(_pdb(90.0, 80.0, 70.0)) == 80.0


def test_CA_가_없으면_재지_못했다고_한다():
    assert mean_plddt("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 90.00\n") is None


def test_보고한_pLDDT_와_파일이_맞으면_맞다고_한다():
    out = check_structure(_pdb(88.0, 88.0), reported_plddt=88.0)

    assert out["plddt_measured"] == 88.0
    assert out["plddt_agrees"] is True
    assert out["plddt_gap"] == 0.0


def test_보고한_pLDDT_와_파일이_다르면_잡아낸다():
    """SFR-007 — 이것이 2차 검증의 본론이다.

    보고된 값은 아래 모든 화면에 그대로 실린다. 여기서 잡지 않으면
    아무도 잡지 않는다.
    """
    out = check_structure(_pdb(40.0, 40.0), reported_plddt=92.0)

    assert out["plddt_agrees"] is False
    assert out["plddt_measured"] == 40.0
    assert out["plddt_reported"] == 92.0
    assert out["plddt_gap"] == 52.0


def test_반올림_차이는_불일치로_보지_않는다():
    out = check_structure(_pdb(88.0, 88.0), reported_plddt=88.0 + PLDDT_TOLERANCE / 2)

    assert out["plddt_agrees"] is True


def test_기준_구조가_없으면_RMSD_를_쟀다고_하지_않는다():
    out = check_structure(_pdb(88.0), reported_plddt=88.0)

    assert "rmsd_measured" not in out
    assert "기준 구조가 없습니다" in out["rmsd_reason"]


def test_같은_구조끼리의_RMSD_는_0_이다():
    pdb = _pdb(90.0, 90.0, 90.0)

    out = check_structure(pdb, reference_pdb=pdb)

    assert out["rmsd_measured"] == 0.0


def test_빈_구조는_확인하지_못했다고_한다():
    out = check_structure("")

    assert out["verified"] is False


# ---------------------------------------------------------------- 연결


def test_MSA_응답을_해석하면_보존도가_따라온다():
    metrics = interpret("msa", {"a3m": A3M, "conservation_tiers": [0.25]})

    assert metrics["depth"] == 3
    assert metrics["fixed_positions"]["0.25"] == 2
    #  원래 응답은 버리지 않는다
    assert metrics["a3m"] == A3M


def test_구조_응답을_해석하면_2차_검증이_따라온다():
    metrics = interpret("af2", {"pdb": _pdb(50.0, 50.0), "plddt": 95.0})

    assert metrics["plddt_agrees"] is False
    assert metrics["plddt_measured"] == 50.0
    #  보고된 값은 그대로 둔다 — 아래 화면이 모델 응답에 없는 수를
    #  보여주게 되면 안 된다
    assert metrics["plddt"] == 95.0


def test_보고된_값이_없으면_잰_값을_쓴다():
    metrics = interpret("af2", {"pdb": _pdb(77.5, 77.5)})

    assert metrics["plddt"] == 77.5


def test_구조가_없는_응답은_그냥_지나간다():
    metrics = interpret("af2", {"note": "아직 없음"})

    assert metrics == {"note": "아직 없음"}
