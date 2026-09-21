"""Surrogate triage.

Predict solubility (SoluProt) and structure confidence (pLDDT) from a cheap ESM
embedding, so a large candidate pool can be cut to a Top-K before the expensive
AF2/SoluProt run - the resource-aware surrogate triage the pipeline is named for.

The two MLPs were trained in meta_surrogate_prototype and exported to the model
directory (settings.surrogate_model_dir). Each takes a 320-d ESM-2 (8M)
embedding per sequence. sklearn and numpy are platform dependencies; torch is
not, so the embeddings come from the ESM endpoint - or, in dev, the mock adapter
fabricates the whole result and this module is never reached.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from foldfront.core.config import get_settings

#  ESM-2 8M (facebook/esm2_t6_8M_UR50D) mean-pooled hidden size. Both MLPs were
#  fit on this width; an embedding of any other width is not what they saw.
EMBED_DIM = 320

SOLUPROT_MODEL = "global_soluprot_v1.pkl"
PLDDT_MODEL = "global_plddt_v1.pkl"


class SurrogateError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_models() -> tuple[Any, Any]:
    """Load the two exported MLPs. Cached: they are read once per process."""
    d = Path(get_settings().surrogate_model_dir)
    solu, plddt = d / SOLUPROT_MODEL, d / PLDDT_MODEL
    missing = [p.name for p in (solu, plddt) if not p.exists()]
    if missing:
        raise SurrogateError(
            f"대리모델 파일이 없습니다: {', '.join(missing)} (경로 {d}). "
            f"meta_surrogate_prototype/08_export_final_models.py 로 내보냅니다."
        )
    with open(solu, "rb") as f:
        m_solu = pickle.load(f)
    with open(plddt, "rb") as f:
        m_plddt = pickle.load(f)
    return m_solu, m_plddt


@dataclass(frozen=True)
class Scored:
    id: str
    soluprot: float
    plddt: float
    score: float


def combined_score(soluprot: float, plddt: float) -> float:
    """SoluProt is a 0..1 probability; pLDDT is 0..100. Bring pLDDT onto the same
    scale and weight them evenly - a candidate has to look both soluble and
    foldable to survive triage."""
    return float((soluprot + plddt / 100.0) / 2.0)


def score(ids: Sequence[str], embeddings: np.ndarray) -> list[Scored]:
    """Predict soluprot and pLDDT for each sequence's embedding, and combine."""
    embeddings = np.asarray(embeddings, dtype=np.float32)
    if embeddings.ndim != 2 or embeddings.shape[1] != EMBED_DIM:
        raise SurrogateError(
            f"임베딩은 (N, {EMBED_DIM}) 형태여야 합니다. 받은 형태 {embeddings.shape}"
        )
    if len(ids) != embeddings.shape[0]:
        raise SurrogateError(
            f"서열 {len(ids)}개와 임베딩 {embeddings.shape[0]}개가 맞지 않습니다"
        )
    m_solu, m_plddt = _load_models()
    solu = np.asarray(m_solu.predict(embeddings), dtype=float)
    plddt = np.asarray(m_plddt.predict(embeddings), dtype=float)
    return [
        Scored(str(i), float(s), float(p), combined_score(float(s), float(p)))
        for i, s, p in zip(ids, solu, plddt)
    ]


def triage(scored: Sequence[Scored], top_k: int) -> tuple[list[Scored], list[Scored]]:
    """Split the candidates into kept (the Top-K by combined score) and pruned.

    Ties break by id, so the same pool and k always split the same way. A top_k
    of 0 or less keeps nothing; a top_k at or above the pool keeps everything.
    """
    ranked = sorted(scored, key=lambda s: (-s.score, s.id))
    k = max(0, top_k)
    return list(ranked[:k]), list(ranked[k:])
