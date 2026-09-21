"""Quality signals: what a stage's numbers say, and what to do about it.

The original reads a finished stage and says whether the result is worth
carrying forward - a shallow alignment, a solubility pass rate near zero, an
average pLDDT too low to trust. Those judgements live in
`pipeline_mcp/agent_panel.py` and they are the domain knowledge this platform
inherits rather than invents.

What this module does is apply them to the metrics *this* platform records.
The thresholds are the original's, quoted beside each rule with the function
they come from; the metric names are ours, because the original reads
`usable_hits` out of a file its own MSA step wrote and we record `depth` from
the adapter's reply. Mapping the names is the whole of the difference.

    upstream _interpret_msa       usable_hits < 50        ->  depth
    upstream _interpret_soluprot  fraction   < 0.2        ->  pass_rate
    upstream _interpret_af2       avg_plddt  < 75.0       ->  plddt

Where we hold no metric the rule reads, the rule says nothing. Silence is the
honest answer: a warning derived from a value nobody measured would be worse
than no warning at all.

A signal is not an error. A stage can succeed and still be worth looking at,
which is the whole point - the run finished, and someone should decide
whether to spend the next GPU hour on what it produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from foldfront.db.models import Run, RunStatus, StageState
from foldfront.engine.binding import (
    CONFIDENCE_GOOD,
    CONFIDENCE_POOR,
    INTERFACE_MINIMAL,
)
from foldfront.engine.verify import PLDDT_TOLERANCE

#  The original's cutoffs, kept as named constants so a reader can see at a
#  glance what was inherited and check it against agent_panel.py.
MSA_DEPTH_LOW = 50           # _interpret_msa: usable_hits < 50
MSA_DEPTH_MODEST = 200       # _interpret_msa: usable_hits < 200
MSA_COVERAGE_LOW = 0.3       # _interpret_msa: median_coverage < 0.3
SOLUPROT_RATE_LOW = 0.2      # _interpret_soluprot: fraction < 0.2
AF2_PLDDT_LOW = 75.0         # _interpret_af2: avg_plddt < 75.0


@dataclass(frozen=True)
class Signal:
    """One reading of one stage."""

    stage: str
    level: str            # info · warning · error
    message: str
    #  What the reader should consider doing. The original phrases these as
    #  suggestions and so do we: the parameter is theirs to change.
    advice: str | None = None
    #  The metric and value the rule fired on, so the reader can check it
    #  rather than take the judgement on trust.
    evidence: dict[str, Any] | None = None
    #  Which of the original's functions this rule came from. Kept so the
    #  inheritance is traceable from the screen back to the source.
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "level": self.level, "message": self.message,
            "advice": self.advice, "evidence": self.evidence or {},
            "source": self.source,
        }


def _num(metrics: dict[str, Any], *names: str) -> float | None:
    """The first of these metrics that is actually a number."""
    for name in names:
        value = metrics.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


Rule = Callable[[StageState, dict[str, Any]], Iterable[Signal]]


def _msa(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    #  The design stage honours the fixed positions, so none of them means
    #  nothing is held and every conserved residue is free to change.
    fixed = m.get("fixed_positions")
    query_len = _num(m, "query_length")
    if isinstance(fixed, dict) and fixed:
        counts = [v for v in fixed.values() if isinstance(v, int)]
        if counts and all(c == 0 for c in counts):
            yield Signal(
                stage.name, "warning",
                "보존 위치를 하나도 찾지 못했습니다.",
                "정렬 품질을 확인하십시오. 고정할 잔기가 없으면 설계가 전부 자유롭게 바뀝니다.",
                {"fixed_positions": fixed},
                "agent_panel._interpret_conservation",
            )
        elif query_len and any(c >= 0.8 * query_len for c in counts):
            yield Signal(
                stage.name, "warning",
                "고정된 위치가 너무 많습니다. 설계 여지가 거의 없습니다.",
                "보존도 tier 를 낮추십시오.",
                {"fixed_positions": fixed, "query_length": query_len},
                "agent_panel._interpret_conservation",
            )

    depth = _num(m, "depth", "usable_hits", "hits")
    if depth is not None:
        if depth < MSA_DEPTH_LOW:
            yield Signal(
                stage.name, "warning",
                f"정렬 깊이가 얕습니다 ({int(depth)}개).",
                "mmseqs_max_seqs 를 늘리거나 대상 데이터베이스를 바꿔 보십시오.",
                {"depth": depth, "threshold": MSA_DEPTH_LOW},
                "agent_panel._interpret_msa",
            )
        elif depth < MSA_DEPTH_MODEST:
            yield Signal(
                stage.name, "info",
                f"정렬 깊이가 넉넉하지 않습니다 ({int(depth)}개).",
                "기준값을 조금만 바꿔도 결과가 달라질 수 있습니다.",
                {"depth": depth, "threshold": MSA_DEPTH_MODEST},
                "agent_panel._interpret_msa",
            )

    coverage = _num(m, "coverage", "median_coverage")
    if coverage is not None and coverage < MSA_COVERAGE_LOW:
        yield Signal(
            stage.name, "warning",
            f"정렬 적용 범위가 좁습니다 ({coverage:.2f}).",
            "입력 서열을 거르거나 다른 입력을 써 보십시오.",
            {"coverage": coverage, "threshold": MSA_COVERAGE_LOW},
            "agent_panel._interpret_msa",
        )


def _soluprot(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    scored = _num(m, "scored", "total")
    if scored is None or scored <= 0:
        return
    passed = _num(m, "passed")
    rate = _num(m, "pass_rate", "fraction")

    if passed is not None and passed <= 0:
        yield Signal(
            stage.name, "error",
            "용해도 기준을 통과한 서열이 없습니다.",
            "soluprot_cutoff 을 낮춰 보십시오.",
            {"scored": scored, "passed": 0},
            "agent_panel._interpret_soluprot",
        )
    elif rate is not None and rate < SOLUPROT_RATE_LOW:
        yield Signal(
            stage.name, "warning",
            f"용해도 통과율이 낮습니다 ({rate:.1%}).",
            "sampling_temp 를 낮추거나 제약을 완화해 보십시오.",
            {"pass_rate": rate, "threshold": SOLUPROT_RATE_LOW},
            "agent_panel._interpret_soluprot",
        )


def _af2(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    #  Checked first, and loudest. A reported pLDDT the returned structure
    #  does not support travels through every screen downstream, and the
    #  screens all show the reported one - so if it is not caught here it
    #  is not caught. Everything after this reads a number in doubt.
    if m.get("plddt_agrees") is False:
        reported = _num(m, "plddt_reported")
        measured = _num(m, "plddt_measured")
        yield Signal(
            stage.name, "error",
            f"보고된 pLDDT 와 구조 파일이 맞지 않습니다 "
            f"(보고 {reported}, 파일에서 잰 값 {measured}).",
            "이 실행의 pLDDT 를 근거로 쓰지 마십시오. 엔드포인트 응답 규격을 확인해야 합니다.",
            {"plddt_reported": reported, "plddt_measured": measured,
             "gap": _num(m, "plddt_gap"), "tolerance": PLDDT_TOLERANCE},
            "foldfront",
        )

    plddt = _num(m, "plddt", "avg_plddt", "mean_plddt")
    if plddt is not None and plddt < AF2_PLDDT_LOW:
        yield Signal(
            stage.name, "warning",
            f"평균 pLDDT 가 낮습니다 ({plddt:.1f}).",
            "구조 예측을 그대로 믿기 어렵습니다. pLDDT·RMSD 기준을 조정해 보십시오.",
            {"plddt": plddt, "threshold": AF2_PLDDT_LOW},
            "agent_panel._interpret_af2",
        )
    selected = _num(m, "selected")
    if selected is not None and selected <= 0:
        yield Signal(
            stage.name, "error",
            "구조 예측에서 고른 설계가 없습니다.",
            "pLDDT·RMSD 기준을 낮추거나 설계 단계를 손보십시오.",
            {"selected": 0},
            "agent_panel._interpret_af2",
        )


def _design(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    count = _num(m, "sequences", "samples")
    if count is not None and count <= 0:
        yield Signal(
            stage.name, "error",
            "서열을 하나도 내지 못했습니다.",
            "고정 잔기 설정과 입력 PDB 를 확인하십시오.",
            {"sequences": 0},
            "agent_panel._interpret_proteinmpnn",
        )


def _docking(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    poses = _num(m, "poses")
    if poses is None:
        return
    if poses <= 0:
        yield Signal(
            stage.name, "error",
            "결합 pose 를 하나도 내지 못했습니다.",
            "리간드 입력과 단백질 구조를 확인하십시오.",
            {"poses": 0}, "foldfront",
        )
        return

    acceptable = _num(m, "acceptable_poses")
    if acceptable is not None and acceptable <= 0:
        yield Signal(
            stage.name, "warning",
            f"pose {int(poses)}개가 모두 기준에 못 미칩니다.",
            "확신도가 낮거나 인터페이스가 없습니다. 결합 부위를 지정하거나 리간드를 확인하십시오.",
            {"poses": poses, "acceptable": 0,
             "confidence_floor": CONFIDENCE_POOR, "interface_floor": INTERFACE_MINIMAL},
            "foldfront",
        )
        return

    #  A pose the model is sure of that buries nothing is a confident
    #  prediction of a weak interaction, and the two numbers have to be
    #  read together or it looks like a hit.
    confidence = _num(m, "best_confidence")
    residues = _num(m, "best_interface_residues")
    if (
        confidence is not None and confidence >= CONFIDENCE_GOOD
        and residues is not None and residues <= INTERFACE_MINIMAL
    ):
        yield Signal(
            stage.name, "warning",
            f"가장 좋은 pose 가 확신도는 높으나 (confidence {confidence:.2f}) "
            f"닿는 잔기가 {int(residues)}개뿐입니다.",
            "자세는 맞을 수 있으나 결합이 약합니다. 확신도만 보고 고르지 마십시오.",
            {"best_confidence": confidence, "best_interface_residues": residues,
             "interface_floor": INTERFACE_MINIMAL},
            "foldfront",
        )


def _rfd3(stage: StageState, m: dict[str, Any]) -> Iterable[Signal]:
    count = _num(m, "backbones")
    if count is not None and count <= 0:
        yield Signal(
            stage.name, "error",
            "백본을 하나도 내지 못했습니다.",
            "rfd3 입력과 엔드포인트를 확인하십시오.",
            {"backbones": 0},
            "agent_panel._interpret_rfd3",
        )


#  By model id, not by node name: a researcher names a node whatever they
#  like, and the judgement belongs to the model that produced the numbers.
RULES: dict[str, Rule] = {
    "msa": _msa,
    "mmseqs": _msa,
    "soluprot": _soluprot,
    "af2": _af2,
    "colabfold": _af2,
    "esmfold": _af2,
    "design": _design,
    "proteinmpnn": _design,
    "rfd3": _rfd3,
    "diffdock": _docking,
}


def read_signals(run: Run) -> list[Signal]:
    """Every signal this run's stages raise, in the order the stages ran."""
    out: list[Signal] = []

    for stage in run.stages:
        if stage.status is RunStatus.FAILED:
            out.append(Signal(
                stage.name, "error",
                f"{stage.name} 단계가 실패했습니다.",
                stage.error or "사건 기록을 확인하십시오.",
                {"error": stage.error},
                "foldfront",
            ))
            continue
        if stage.status is not RunStatus.SUCCEEDED or not stage.metrics:
            continue

        #  A mock adapter's numbers are not evidence, and a panel that reads
        #  them as though they were is how a demo turns into a claim.
        if stage.metrics.get("_mock"):
            out.append(Signal(
                stage.name, "info",
                "모의 어댑터가 낸 값입니다. 판정하지 않습니다.",
                "실제 엔드포인트로 다시 실행해야 값이 근거가 됩니다.",
                {"_mock": True},
                "foldfront",
            ))
            continue

        rule = RULES.get(stage.model_id or stage.name)
        if rule is None:
            continue
        out.extend(rule(stage, dict(stage.metrics)))

    return out
