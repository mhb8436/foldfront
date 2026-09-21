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
