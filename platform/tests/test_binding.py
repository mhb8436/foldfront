"""결합 예측 — 인터페이스 · 점수 · 랭킹.

인터페이스 검출은 원본 `ligand_proximity_mask` 다. 점수와 랭킹은 원본에
없어 여기서 세웠고, 그래서 **값을 지어내지 않았는지**가 시험의 초점이다.
"""

from __future__ import annotations

import pytest

from foldfront.engine.binding import (
    CONFIDENCE_GOOD,
    CONFIDENCE_POOR,
    INTERFACE_ANGSTROM,
    INTERFACE_MINIMAL,
    Interface,
    rank_poses,
    read_docking,
    read_interface,
    score_pose,
)
from foldfront.engine.results import interpret

pytest.importorskip("pipeline_mcp.bio.pdb")


def _complex(contacts: int) -> str:
    """단백질 잔기 20개와 리간드 하나.

    contacts 개는 리간드를 둘러싸듯 반지름 3 Å 껍질에 놓아 기본 6 Å 안에
    들게 하고, 나머지는 멀리 보낸다. 한 줄로 늘어놓으면 거리 기준에 걸려
    앞쪽 몇 개만 세어지므로 포켓처럼 배치한다.
    """
    import math

    lines = []
    for i in range(1, 21):
        if i <= contacts:
            angle = 2.0 * math.pi * i / max(1, contacts)
            x, y, z = 3.0 * math.cos(angle), 3.0 * math.sin(angle), (i % 3) - 1.0
        else:
            x, y, z = 500.0 + i, 0.0, 0.0
        lines.append(
            f"ATOM  {i:5d}  CA  ALA A{i:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{50.0:6.2f}           C"
        )
    lines.append(
        f"HETATM{99:5d}  C1  LIG B{1:4d}    "
        f"{0.0:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{50.0:6.2f}           C"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 인터페이스


def test_리간드에_닿는_잔기를_센다():
    face = read_interface(_complex(contacts=4))

    assert face.count == 4
    assert face.chains == ["A"]
    assert face.distance_angstrom == INTERFACE_ANGSTROM


def test_기본_거리는_원본의_것이다():
    """4.0 이 아니라 6.0 인 까닭 — 원본 마스킹 단계와 같은 수를 내야 한다."""
    assert INTERFACE_ANGSTROM == 6.0


def test_거리를_좁히면_닿는_잔기가_줄어든다():
    wide = read_interface(_complex(contacts=8), distance_angstrom=6.0)
    narrow = read_interface(_complex(contacts=8), distance_angstrom=2.0)

    assert narrow.count < wide.count


def test_리간드가_없으면_인터페이스도_없다():
    only_protein = "\n".join(
        f"ATOM  {i:5d}  CA  ALA A{i:4d}    {i:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 50.00           C"
        for i in range(1, 5)
    )

    assert read_interface(only_protein).count == 0


def test_빈_구조는_빈_인터페이스다():
    assert read_interface("").count == 0


# ---------------------------------------------------------------- 점수


def test_확신도와_인터페이스를_함께_본다():
    good = score_pose(confidence=1.0, interface=Interface({"A": list(range(20))}))
    weak = score_pose(confidence=1.0, interface=Interface({"A": [1, 2]}))

    #  같은 확신도라도 아무것도 묻지 않는 pose 는 낮다
    assert good["score"] > weak["score"]


def test_점수의_구성_요소를_함께_낸다():
    """뜯어볼 수 없는 순위는 통째로 믿는 수밖에 없고, 아무도 그러지 않는다."""
    out = score_pose(confidence=0.5, interface=Interface({"A": list(range(12))}))

    assert set(out["components"]) == {"confidence", "interface"}
    assert out["weights"]["confidence"] > 0
    assert out["scored_on"] == ["confidence", "interface"]


def test_점수를_친화도라고_부르지_않는다():
    """DiffDock 은 결합 세기를 예측하지 않는다. 단위를 붙이면 지어낸 값이 된다."""
    out = score_pose(confidence=0.5, interface=Interface({"A": [1]}))

    assert "affinity" not in out["score_is"].lower().replace("not a binding affinity", "")
    assert "ranking key" in out["score_is"]


def test_확신도가_없으면_0점이_아니라_빼고_센다():
    """0 으로 세면 「모르는 pose」가 「모델이 의심한 pose」보다 아래가 된다."""
    unknown = score_pose(confidence=None, interface=Interface({"A": list(range(20))}))
    doubted = score_pose(confidence=-3.0, interface=Interface({"A": list(range(20))}))

    assert unknown["scored_on"] == ["interface"]
    assert unknown["score"] > doubted["score"]


def test_확신도_구간은_DiffDock_저자들의_것이다():
    assert CONFIDENCE_GOOD == 0.0
    assert CONFIDENCE_POOR == -1.5


def test_가중치를_바꾸면_순서가_바뀐다():
    """값은 이 플랫폼이 정할 것이 아니다. 그래서 인자다."""
    confident_shallow = {"confidence": 1.5, "complex_pdb": _complex(contacts=2)}
    unsure_deep = {"confidence": -0.5, "complex_pdb": _complex(contacts=15)}

    by_confidence = rank_poses([confident_shallow, unsure_deep],
                               weights={"confidence": 1.0, "interface": 0.0})
    by_interface = rank_poses([confident_shallow, unsure_deep],
                              weights={"confidence": 0.0, "interface": 1.0})

    assert by_confidence[0]["confidence"] == 1.5
    assert by_interface[0]["confidence"] == -0.5


# ---------------------------------------------------------------- 랭킹


def test_점수순으로_다시_매기고_원래_순위를_남긴다():
    poses = [
        {"rank": 1, "confidence": -2.0, "complex_pdb": _complex(contacts=1)},
        {"rank": 2, "confidence": 1.0, "complex_pdb": _complex(contacts=15)},
    ]

    ranked = rank_poses(poses)

    assert ranked[0]["rank"] == 1 and ranked[0]["reported_rank"] == 2
    assert ranked[1]["rank"] == 2 and ranked[1]["reported_rank"] == 1


def test_점수를_못_매긴_pose_도_버리지_않는다():
    """돌아온 pose 는 사실이다. 숨기면 개수가 엔드포인트 응답과 어긋난다."""
    ranked = rank_poses([
        {"confidence": 1.0, "complex_pdb": _complex(contacts=15)},
        {"confidence": None, "complex_pdb": ""},
    ])

    assert len(ranked) == 2


# ---------------------------------------------------------------- 연결


def test_도킹_응답을_해석하면_순위가_따라온다():
    metrics = interpret("diffdock", {"poses": [
        {"confidence": 1.2, "complex_pdb": _complex(contacts=15)},
        {"confidence": -2.0, "complex_pdb": _complex(contacts=1)},
    ]})

    assert metrics["poses"] == 2
    assert metrics["best_confidence"] == 1.2
    assert metrics["best_interface_residues"] == 15
    assert metrics["acceptable_poses"] == 1


def test_분기_조건이_읽을_수_있는_수를_낸다():
    """조건식은 값을 경로로 읽는다. diffdock.acceptable_poses > 0 이 되어야 한다."""
    metrics = interpret("diffdock", {"poses": [
        {"confidence": -3.0, "complex_pdb": _complex(contacts=1)},
    ]})

    assert metrics["acceptable_poses"] == 0


def test_pose_가_하나뿐인_응답도_읽는다():
    """원본 러너는 상위 pose 하나만 남긴다."""
    metrics = read_docking({"confidence": 0.8, "complex_pdb": _complex(contacts=15)})

    assert metrics["poses"] == 1


def test_도킹_결과가_없으면_아무_말도_하지_않는다():
    assert read_docking({"note": "아직"}) == {}
