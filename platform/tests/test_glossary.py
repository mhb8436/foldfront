"""용어집 시험 — 세 벌이 어긋나지 않게 잡는다.

`docs/용어집.md` 가 기준이고, 화면(`web/src/lib/glossary.ts`)과 Copilot
(`engine/glossary.py`)이 같은 말을 옮겨 쓴다. 세 벌은 하나 많은 것이고,
어긋나면 화면과 Copilot 이 같은 용어를 다르게 설명하게 된다.

문서를 실행 중에 읽지 않는 까닭은 산문 파일을 API 의 실행 의존성으로 만들지
않기 위해서다. 대신 여기서 대조한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from foldfront.engine.glossary import (
    METRIC_TERMS,
    STAGE_TERMS,
    metric_term,
    relevant_terms,
    stage_term,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "용어집.md"
CONSOLE = ROOT / "web" / "src" / "lib" / "glossary.ts"


def _doc_labels() -> dict[str, str]:
    """식별자와 「읽는 이름」을 뽑는다.

    용어집에는 표가 두 종류다. 단계 표는 `| 단계 | 읽는 이름 | 하는 일 | … |`
    로 이름 칸이 따로 있고, 딸린 표들은 `| 식별자 | 설명 |` 두 칸뿐이다.
    두 칸짜리의 둘째 칸은 이름이 아니라 설명이므로 이름 대조에 쓰지 않는다.
    """
    out: dict[str, str] = {}
    if not DOC.is_file():
        return out
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        ids = re.findall(r"`([a-z0-9_]+)`", cells[0])
        label = cells[1].strip()
        #  구분선(---)과 머리글은 건너뛴다
        if ids and label and not set(label) <= {"-", ":"}:
            for i in ids:
                out.setdefault(i, label)
    return out


def _console_labels() -> dict[str, str]:
    if not CONSOLE.is_file():
        return {}
    text = CONSOLE.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\s{2}([a-z0-9_]+):\s*\{\s*label:\s*'([^']+)'", text, re.M)
    }


def test_용어집_문서를_읽어낸다():
    """읽지 못하면 아래 대조가 공허하게 참이 된다."""
    assert len(_doc_labels()) >= 10, "용어집에서 용어를 뽑지 못했다 — 표 형식이 바뀌었는가"


@pytest.mark.parametrize("key", sorted(STAGE_TERMS))
def test_단계_용어가_문서와_같은_이름을_쓴다(key: str):
    doc = _doc_labels()
    if key not in doc:
        pytest.skip(f"{key} 는 용어집 표에 없다")
    assert STAGE_TERMS[key].label == doc[key], (
        f"{key}: Copilot 은 「{STAGE_TERMS[key].label}」, 문서는 「{doc[key]}」"
    )


@pytest.mark.parametrize("key", sorted(STAGE_TERMS))
def test_단계_용어가_화면과_같은_이름을_쓴다(key: str):
    console = _console_labels()
    if key not in console:
        pytest.skip(f"{key} 는 화면 용어집에 없다")
    assert STAGE_TERMS[key].label == console[key], (
        f"{key}: Copilot 은 「{STAGE_TERMS[key].label}」, 화면은 「{console[key]}」"
    )


@pytest.mark.parametrize("key", sorted(METRIC_TERMS))
def test_지표_용어가_화면과_같은_이름을_쓴다(key: str):
    console = _console_labels()
    if key not in console:
        pytest.skip(f"{key} 는 화면 용어집에 없다")
    assert METRIC_TERMS[key].label == console[key]


def test_모든_용어에_설명이_붙어_있다():
    """이름만 있는 용어는 설명을 물었을 때 답이 되지 않는다."""
    for key, term in {**STAGE_TERMS, **METRIC_TERMS}.items():
        assert term.hint.strip(), f"{key} 에 설명이 없다"


def test_단계로_찾는다():
    assert stage_term("soluprot").label == "가용성 예측"
    assert stage_term("없는단계") is None
    assert stage_term(None) is None


def test_단계로_한정한_지표_이름도_찾는다():
    """조건식은 `soluprot.pass_rate` 처럼 단계를 앞에 붙여 쓴다."""
    assert metric_term("soluprot.pass_rate") is metric_term("pass_rate")
    assert metric_term("pass_rate").label == "가용성 통과율"


def test_실행에_나온_용어만_추린다():
    """사전 전체를 주면 모델이 그 실행에 없던 용어를 끌어다 쓴다."""
    picked = relevant_terms({"msa", "plddt", "diffdock"})

    assert set(picked) == {"msa", "plddt", "diffdock"}
    assert "가용성 통과율" not in " ".join(picked.values())


def test_모르는_이름은_넣지_않는다():
    assert relevant_terms({"우리가_모르는_것"}) == {}


def test_설명이_이름과_함께_나온다():
    """Copilot 이 「pLDDT 는 예측 신뢰도이며 …」 라고 답할 수 있어야 한다."""
    picked = relevant_terms({"plddt"})

    assert picked["plddt"].startswith("예측 신뢰도 — ")
