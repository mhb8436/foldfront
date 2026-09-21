"""Surrogate triage: scoring, the Top-K split, and the local adapter.

The scoring tests load the real exported MLPs (pipeline-mcp/models). The adapter
test injects a deterministic embedder, so it exercises the whole score -> triage
-> kept-FASTA path without an ESM endpoint.
"""

import numpy as np
import pytest

from foldfront.db.models import ResourceSpec
from foldfront.engine import surrogate
from foldfront.engine.adapters import AdapterError, SurrogateAdapter
from foldfront.engine.payloads import build_payload
from foldfront.engine.results import interpret
from foldfront.engine.router import Route


def _route() -> Route:
    return Route(
        model_id="surrogate", version="v1", transport="local", target="surrogate",
        resources=ResourceSpec(), timeout_seconds=60.0,
    )


def test_combined_score_puts_plddt_on_the_soluprot_scale():
    #  soluprot 0..1, pLDDT 0..100. A perfect pair scores 1.0, a zero pair 0.0.
    assert surrogate.combined_score(1.0, 100.0) == pytest.approx(1.0)
    assert surrogate.combined_score(0.0, 0.0) == pytest.approx(0.0)
    assert surrogate.combined_score(0.5, 50.0) == pytest.approx(0.5)


def test_triage_keeps_top_k_by_score():
    scored = [
        surrogate.Scored("a", 0.9, 90, 0.9),
        surrogate.Scored("b", 0.2, 40, 0.3),
        surrogate.Scored("c", 0.6, 70, 0.65),
    ]
    kept, pruned = surrogate.triage(scored, 2)
    assert [s.id for s in kept] == ["a", "c"]
    assert [s.id for s in pruned] == ["b"]


def test_triage_edges_keep_none_and_keep_all():
    scored = [surrogate.Scored("a", 0.5, 50, 0.5), surrogate.Scored("b", 0.4, 40, 0.4)]
    kept0, pruned0 = surrogate.triage(scored, 0)
    assert kept0 == [] and len(pruned0) == 2
    kept_all, pruned_all = surrogate.triage(scored, 5)
    assert len(kept_all) == 2 and pruned_all == []


def test_score_loads_the_real_models_and_ranks():
    rng = np.random.default_rng(0)
    emb = rng.standard_normal((5, surrogate.EMBED_DIM)).astype(np.float32)
    scored = surrogate.score([f"s{i}" for i in range(5)], emb)
    assert len(scored) == 5
    assert all(np.isfinite(s.score) for s in scored)
    #  The combined score is the average the module documents.
    for s in scored:
        assert s.score == pytest.approx(surrogate.combined_score(s.soluprot, s.plddt))


def test_score_rejects_the_wrong_embedding_width():
    with pytest.raises(surrogate.SurrogateError):
        surrogate.score(["a"], np.zeros((1, 8), dtype=np.float32))


def test_score_rejects_a_count_mismatch():
    with pytest.raises(surrogate.SurrogateError):
        surrogate.score(["a", "b"], np.zeros((1, surrogate.EMBED_DIM), dtype=np.float32))


async def test_adapter_scores_triages_and_hands_on_only_the_kept():
    #  A deterministic embedder stands in for the ESM endpoint: distinct rows so
    #  the two candidates score differently and the split is stable.
    def embed(seqs):
        return np.array(
            [[float(i)] * surrogate.EMBED_DIM for i in range(len(seqs))], dtype=np.float32
        )

    adapter = SurrogateAdapter(embed=embed)
    payload = {
        "items": [
            {"id": "d1", "sequence": "MKV"},
            {"id": "d2", "sequence": "AAG"},
            {"id": "d3", "sequence": "GGG"},
        ],
        "top_k": 2,
    }
    result = await adapter.invoke(_route(), payload)

    assert result["kept_count"] == 2
    assert result["pruned_count"] == 1
    assert len(result["kept"]) == 2 and len(result["pruned"]) == 1
    #  The kept sequences come back as designed_fasta, so the next stage reads a
    #  smaller pool. Every kept id appears, no pruned id does.
    for kid in result["kept"]:
        assert f">{kid}" in result["designed_fasta"]
    for pid in result["pruned"]:
        assert f">{pid}" not in result["designed_fasta"]


async def test_adapter_refuses_an_empty_pool():
    adapter = SurrogateAdapter(embed=lambda s: np.zeros((0, surrogate.EMBED_DIM)))
    with pytest.raises(AdapterError):
        await adapter.invoke(_route(), {"items": [], "top_k": 2})


def test_payload_reads_designed_sequences_and_top_k():
    p = build_payload(
        "surrogate",
        {"triage_top_k": 3},
        {"designed_fasta": ">d1\nMKV\n>d2\nAAG\n"},
    )
    assert p["top_k"] == 3
    assert [it["id"] for it in p["items"]] == ["d1", "d2"]


def test_interpret_summarises_the_pool():
    reply = {
        "kept_count": 2,
        "pruned_count": 3,
        "scored": [
            {"id": "a", "soluprot": 0.8, "plddt": 90.0, "score": 0.85},
            {"id": "b", "soluprot": 0.4, "plddt": 50.0, "score": 0.45},
        ],
    }
    out = interpret("surrogate", reply)
    assert out["kept_count"] == 2 and out["pruned_count"] == 3
    assert out["mean_soluprot"] == pytest.approx(0.6, abs=1e-6)
    assert out["mean_plddt"] == pytest.approx(70.0, abs=1e-6)
