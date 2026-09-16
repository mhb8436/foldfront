"""모델별 실행 입력 구성.

어댑터는 **어디로 보낼지**를 알고(RunPod·HTTP·컨테이너), 여기는 **무엇을 보낼지**를 안다.
둘을 갈라 두면 새 모델을 붙일 때 전송 코드를 건드리지 않는다.

입력 형태는 지어내지 않는다. 원본 `pipeline_mcp.clients.*` 가 실제로 보내던 모양을 그대로 쓴다 —
엔드포인트가 그 필드 이름으로 받도록 배포되어 있기 때문이다. 서열·구조 파싱도
원본 `pipeline_mcp.bio` 를 **직접 호출**한다. 같은 파일을 두 번 해석하지 않는다.

★ 실제 엔드포인트 연결은 자격 증명과 엔드포인트 ID 가 확정된 뒤다.
  이 모듈까지가 그 전에 확정할 수 있는 범위다.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline_mcp.bio.fasta import parse_fasta, to_fasta


class PayloadError(ValueError):
    """입력이 모자라거나 형식이 맞지 않는다. 실행 전에 잡는다."""


@dataclass(frozen=True)
class StageInput:
    """한 단계가 받는 것.

    `request` 는 실행 요청 원본이고 `upstream` 은 앞 단계들이 낸 결과다.
    노드가 어느 단계인지에 따라 필요한 것만 꺼내 쓴다.
    """

    request: dict[str, Any]
    upstream: dict[str, Any]

    def path(self, *keys: str) -> str | None:
        for k in keys:
            v = self.request.get(k) or self.upstream.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return None

    def text(self, *keys: str) -> str | None:
        """경로면 읽고, 내용이면 그대로 쓴다. 앞 단계는 대개 내용을 넘긴다."""
        raw = self.path(*keys)
        if raw is None:
            return None
        if "\n" in raw or raw.lstrip().startswith(">"):
            return raw
        p = Path(raw)
        if not p.is_file():
            raise PayloadError(f"입력 파일이 없다: {raw}")
        return p.read_text(encoding="utf-8", errors="replace")


def _b64(text: str) -> str:
    """원본 `_b64encode_text` 와 같다. 엔드포인트가 base64 로 받는다."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _require(value: Any, what: str) -> Any:
    if value in (None, "", [], {}):
        raise PayloadError(f"{what} 이(가) 필요하다")
    return value


# ---------------------------------------------------------------- 모델별 구성

def _mmseqs(si: StageInput) -> dict[str, Any]:
    """MSA. 대상 서열 하나로 정렬을 뜬다."""
    fasta = _require(si.text("target_fasta", "fasta"), "대상 서열")
    records = parse_fasta(fasta)
    if not records:
        raise PayloadError("FASTA 에서 서열을 찾지 못했다")
    return {"fasta": to_fasta(records[:1]), "sequence_count": 1}


def _rfd3(si: StageInput) -> dict[str, Any]:
    """백본 생성."""
    pdb = _require(si.text("target_pdb", "pdb"), "대상 구조")
    return {
        "pdb_base64": _b64(pdb),
        "design_chains": list(si.request.get("design_chains") or []),
        "num_designs": int(si.request.get("num_designs") or 8),
    }


def _proteinmpnn(si: StageInput) -> dict[str, Any]:
    """서열 설계. 원본 `clients/proteinmpnn.py` 의 필드 이름을 그대로 쓴다."""
    pdb = _require(si.text("backbone_pdb", "target_pdb", "pdb"), "백본 구조")
    payload: dict[str, Any] = {
        "pdb_base64": _b64(pdb),
        "pdb_name": str(si.request.get("pdb_name") or "input"),
        "use_soluble_model": bool(si.request.get("use_soluble_model", True)),
        "model_name": str(si.request.get("mpnn_model") or "v_48_020"),
        "num_seq_per_target": int(si.request.get("num_seq_per_target") or 8),
        "batch_size": int(si.request.get("batch_size") or 1),
        "sampling_temp": float(si.request.get("sampling_temp") or 0.1),
        "seed": int(si.request.get("seed") or 0),
        "backbone_noise": float(si.request.get("backbone_noise") or 0.0),
        "cleanup": True,
    }
    chains = si.request.get("design_chains")
    if chains:
        payload["pdb_path_chains"] = " ".join(str(c) for c in chains)
    return payload


def _soluprot(si: StageInput) -> dict[str, Any]:
    """가용성 예측. 설계된 서열 전부를 한 번에 보낸다."""
    fasta = _require(si.text("designed_fasta", "target_fasta", "fasta"), "설계 서열")
    records = parse_fasta(fasta)
    if not records:
        raise PayloadError("FASTA 에서 서열을 찾지 못했다")
    return {"sequences": [{"id": r.id, "sequence": r.sequence} for r in records]}


def _af2(si: StageInput) -> dict[str, Any]:
    """구조 예측. MSA 가 있으면 함께 보낸다."""
    fasta = _require(si.text("designed_fasta", "target_fasta", "fasta"), "예측 대상 서열")
    records = parse_fasta(fasta)
    payload: dict[str, Any] = {
        "fasta": to_fasta(records),
        "num_models": int(si.request.get("af2_num_models") or 1),
        "num_recycles": int(si.request.get("af2_num_recycles") or 3),
    }
    a3m = si.text("msa_a3m", "a3m")
    if a3m:
        payload["a3m_base64"] = _b64(a3m)
    return payload


BUILDERS: dict[str, Callable[[StageInput], dict[str, Any]]] = {
    "mmseqs": _mmseqs,
    "msa": _mmseqs,
    "rfd3": _rfd3,
    "proteinmpnn": _proteinmpnn,
    "design": _proteinmpnn,
    "soluprot": _soluprot,
    "af2": _af2,
}


def build_payload(model_id: str, request: dict[str, Any], upstream: dict[str, Any] | None = None) -> dict[str, Any]:
    """모델 식별자에 맞는 입력을 만든다.

    구성기가 없는 모델은 요청을 그대로 넘긴다 — 사용자 정의 모델은 자기 스키마를 갖는다.
    """
    builder = BUILDERS.get(model_id)
    si = StageInput(request=request or {}, upstream=upstream or {})
    if builder is None:
        return dict(si.request)
    return builder(si)


def known_models() -> tuple[str, ...]:
    return tuple(sorted(BUILDERS))
