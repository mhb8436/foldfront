"""Stage and metric terms, for the copilot to explain them from.

`docs/용어집.md` is the reference and `web/src/lib/glossary.ts` carries the
same wording for the screens. This is the third copy, and it exists because
the copilot answers only from facts it is handed - a question like "pLDDT 가
뭡니까" has to be answerable from the context or it is answered with "현황에
없어 알 수 없습니다", which is true but useless.

Three copies is one more than anyone wants. What keeps them from drifting is
`test_glossary.py`, which reads the Korean names out of `docs/용어집.md` and
fails if a term here says something different. The alternative - parsing the
document at run time - would make a prose file a runtime dependency of the
API, and a heading someone reorders would take the copilot down.

Domain wording is the original's to define. These are reading aids for its
output, not definitions of the science: see CLAUDE.md, 도메인 로직은 원본을
승계하며 수정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    label: str
    hint: str


#  Pipeline stages, by the identifier the registry and the logs use.
STAGE_TERMS: dict[str, Term] = {
    "msa": Term("다중서열정렬", "닮은 서열을 모아 정렬한다. 뒤 단계의 판단 근거가 된다"),
    "mmseqs": Term("다중서열정렬", "닮은 서열을 모아 정렬한다. 뒤 단계의 판단 근거가 된다"),
    "rfd3": Term("백본 생성", "서열 없이 3차원 뼈대 모양만 여러 개 만든다"),
    "bioemu": Term("구조 앙상블", "한 구조가 취할 수 있는 여러 자세를 표본으로 뽑는다"),
    "design": Term("서열 설계", "뼈대를 주고 그 모양이 되는 아미노산 서열을 찾는다"),
    "proteinmpnn": Term("서열 설계", "뼈대를 주고 그 모양이 되는 아미노산 서열을 찾는다"),
    "soluprot": Term("가용성 예측", "대장균에서 녹는지를 서열만 보고 점수로 낸다"),
    "af2": Term("구조 예측", "설계한 서열이 의도한 모양으로 접히는지 확인한다"),
    "colabfold": Term("구조 예측", "설계한 서열이 의도한 모양으로 접히는지 확인한다"),
    "novelty": Term("신규성 평가", "설계 결과가 야생형과 얼마나 다른지 센다"),
    "diffdock": Term("도킹", "단백질과 화합물이 어떻게 맞물리는지 예측한다"),
    "esm": Term("서열 임베딩", "서열을 수치 벡터로 바꾼다. 유사도 비교에 쓴다"),
}

#  What a metric means, so a column of numbers is not read blind.
METRIC_TERMS: dict[str, Term] = {
    "depth": Term("정렬 깊이", "모은 유사 서열 수. 많을수록 근거가 두텁다"),
    "coverage": Term("정렬 범위", "대상 서열 중 정렬된 비율"),
    "backbones": Term("뼈대 수", "만든 백본 후보 수"),
    "mean_rmsd": Term("평균 편차", "기준 구조와의 평균 거리(Å). 낮을수록 가깝다"),
    "sequences": Term("설계 서열 수", "설계한 후보 서열 수"),
    "pass_rate": Term("가용성 통과율", "가용성 기준을 넘긴 후보 비율"),
    "passed": Term("통과 수", "다음 단계로 넘어간 후보 수"),
    "plddt": Term("예측 신뢰도", "0~100. 구조가 좋다가 아니라 예측이 확실하다는 뜻이다"),
    "rmsd": Term("구조 편차", "의도한 구조와의 거리(Å). 낮을수록 의도대로 접혔다"),
    "novel": Term("신규 잔기 수", "야생형과 다른 자리 수"),
}


def stage_term(identifier: str | None) -> Term | None:
    return STAGE_TERMS.get(identifier) if identifier else None


def metric_term(key: str) -> Term | None:
    """Accepts a stage-qualified name such as `soluprot.pass_rate`."""
    bare = key.rsplit(".", 1)[-1] if "." in key else key
    return METRIC_TERMS.get(bare)


def relevant_terms(names: set[str]) -> dict[str, str]:
    """The terms this run actually involves, as `identifier: 이름 — 설명`.

    Narrowed to what appeared, rather than the whole dictionary. The copilot
    context has a budget and a run of four stages does not need the docking
    entry; more to the point, a model handed every term tends to reach for
    one the run never used.
    """
    out: dict[str, str] = {}
    for name in sorted(names):
        term = stage_term(name) or metric_term(name)
        if term:
            out[name] = f"{term.label} — {term.hint}"
    return out
