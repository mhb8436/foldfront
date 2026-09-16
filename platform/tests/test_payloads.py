"""모델 입력 구성 시험.

구성 결과가 **원본 클라이언트가 실제로 보내던 필드 이름**과 같은지 본다.
엔드포인트가 그 이름으로 받도록 배포되어 있으므로 이름이 바뀌면 실연결 때 조용히 깨진다.
"""

from __future__ import annotations

import base64

import pytest

from foldfront.engine.payloads import PayloadError, build_payload, known_models


def _decoded(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def test_msa_는_대상_서열_하나만_보낸다():
    p = build_payload("mmseqs", {"target_fasta": ">t1 설명\nMKVAA\n>t2\nGGGG\n"})

    assert p["sequence_count"] == 1
    assert p["fasta"].startswith(">t1")
    assert "GGGG" not in p["fasta"]


def test_설계는_원본_필드_이름을_그대로_쓴다():
    """원본 clients/proteinmpnn.py 의 payload 키와 일치해야 한다."""
    p = build_payload("proteinmpnn", {"target_pdb": "ATOM      1  N\n", "design_chains": ["A", "B"]})

    assert {
        "pdb_base64", "pdb_name", "use_soluble_model", "model_name",
        "num_seq_per_target", "batch_size", "sampling_temp", "seed",
        "backbone_noise", "cleanup",
    } <= set(p)
    assert _decoded(p["pdb_base64"]).startswith("ATOM")
    #  원본은 체인을 공백으로 이어 보낸다
    assert p["pdb_path_chains"] == "A B"


def test_설계_체인을_주지_않으면_해당_필드를_넣지_않는다():
    """빈 값을 보내면 엔드포인트가 전 체인을 고정으로 읽을 수 있다. 아예 빼는 편이 안전하다."""
    #  A real record line: a bare word with no whitespace reads as a path.
    p = build_payload("proteinmpnn", {"target_pdb": "ATOM      1  N\n"})

    assert "pdb_path_chains" not in p


def test_가용성은_서열_전부를_한_번에_보낸다():
    p = build_payload("soluprot", {"designed_fasta": ">d1\nMKV\n>d2\nAAG\n"})

    assert p["sequences"] == [
        {"id": "d1", "sequence": "MKV"},
        {"id": "d2", "sequence": "AAG"},
    ]


def test_구조_예측은_msa_가_있으면_함께_보낸다():
    p = build_payload("af2", {"designed_fasta": ">d1\nMKV\n", "msa_a3m": ">q\nMKV\n"})

    assert "a3m_base64" in p
    assert _decoded(p["a3m_base64"]).startswith(">q")


def test_msa_가_없으면_그_필드를_빼고_보낸다():
    p = build_payload("af2", {"designed_fasta": ">d1\nMKV\n"})

    assert "a3m_base64" not in p


def test_앞_단계_결과를_입력으로_받는다():
    """설계 단계는 앞의 백본 생성이 낸 구조를 문다."""
    p = build_payload("design", {}, {"backbone_pdb": "ATOM      1  CA\n"})

    assert _decoded(p["pdb_base64"]).startswith("ATOM")


def test_필요한_입력이_없으면_실행_전에_막는다():
    with pytest.raises(PayloadError):
        build_payload("soluprot", {})


def test_없는_파일을_가리키면_사유를_낸다(tmp_path, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        with pytest.raises(PayloadError) as caught:
            build_payload("mmseqs", {"target_fasta": "inputs/없는.fasta"})
    finally:
        get_settings.cache_clear()

    assert "입력 파일이 없습니다" in str(caught.value)
    assert "없는.fasta" in str(caught.value)


def test_구성기가_없는_모델은_요청을_그대로_넘긴다():
    """사용자 정의 모델은 자기 스키마를 갖는다. 임의로 바꾸지 않는다."""
    req = {"임의필드": 1, "another": "x"}

    assert build_payload("우리모델", req) == req


def test_승계한_단계가_모두_구성기를_갖는다():
    assert {"mmseqs", "rfd3", "proteinmpnn", "soluprot", "af2"} <= set(known_models())
