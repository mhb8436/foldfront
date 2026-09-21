"""Builds the input each model expects.

An adapter knows *where* to send work; this knows *what* to send. Keeping them
apart means adding a model does not touch the transport code.

The shapes are not invented. They are what the original clients actually send,
because the endpoints are deployed to read those field names - a rename here
would fail on first contact and look like a network problem. Sequence and
structure parsing calls the original bio helpers rather than parsing the same
file a second time.

Connecting real endpoints waits on credentials and endpoint ids. This module is
as far as that can be prepared beforehand.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline_mcp.bio.fasta import parse_fasta, to_fasta

from foldfront.core.config import get_settings


class PayloadError(ValueError):
    """The input is missing or malformed. Caught before a job is dispatched."""


def _records(fasta: str):
    """parse_fasta raises ValueError on malformed input. A bad input is a
    payload problem, and the worker knows what to do with one of those."""
    try:
        return parse_fasta(fasta)
    except ValueError as exc:
        raise PayloadError(f"FASTA 를 읽지 못했습니다: {exc}") from exc


@dataclass(frozen=True)
class StageInput:
    """What one stage receives.

    `request` is the run request as submitted; `upstream` is what earlier
    stages produced. Each builder takes only what its stage needs.
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
        """Read a path, or take the content as it stands. Earlier stages
        usually pass content rather than write a file.

        Content or path is decided by whitespace, on the trimmed value. A
        path has none; content - a FASTA header line, an ATOM record, an
        alignment - always does. Deciding by newline alone got both edges
        wrong: a path typed into a textarea arrives with a newline after it
        and read as content, and a one-line record with the newline trimmed
        read as a path. The console applies the same rule (web/src/lib/bio.ts).

        A path is only ever read from inside the storage root. This is the one
        place a request string becomes a file read, and without the check a
        request naming /etc/passwd - or another user's upload - would have its
        FASTA headers published in the run's metrics for anyone to see.
        """
        raw = self.path(*keys)
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith(">"):
            return raw
        if re.search(r"\s", raw):
            #  Content - unless it is one line shaped like a path, in which case
            #  it is a path with a space in it, and shipping that string to a
            #  model as a structure would burn a GPU job on eleven bytes.
            if "\n" not in raw and "/" in raw and len(raw) < 512:
                raise PayloadError("경로에 공백이 있습니다. 파일을 올리거나 공백 없는 경로를 쓰십시오")
            return raw
        root = Path(get_settings().output_root).resolve()
        target = (root / raw).resolve()
        if not target.is_relative_to(root):
            raise PayloadError("저장 위치 밖의 파일은 읽지 않습니다")
        if not target.is_file():
            raise PayloadError(f"입력 파일이 없습니다: {target.relative_to(root)}")
        return target.read_text(encoding="utf-8", errors="replace")


def _b64(text: str) -> str:
    """As the original _b64encode_text. The endpoints read base64."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _require(value: Any, what: str) -> Any:
    if value in (None, "", [], {}):
        raise PayloadError(f"{what} 이(가) 필요합니다")
    return value


# ---------------------------------------------------------------- per model

def _mmseqs(si: StageInput) -> dict[str, Any]:
    """MSA. One target sequence goes in; an alignment comes back."""
    fasta = _require(si.text("target_fasta", "fasta"), "대상 서열")
    records = _records(fasta)
    if not records:
        raise PayloadError("FASTA 에서 서열을 찾지 못했습니다")
    return {"fasta": to_fasta(records[:1]), "sequence_count": 1}


def _rfd3(si: StageInput) -> dict[str, Any]:
    """Backbone generation."""
    pdb = _require(si.text("target_pdb", "pdb"), "대상 구조")
    return {
        "pdb_base64": _b64(pdb),
        "design_chains": list(si.request.get("design_chains") or []),
        "num_designs": int(si.request.get("num_designs") or 8),
    }


def _proteinmpnn(si: StageInput) -> dict[str, Any]:
    """Sequence design, using the field names clients/proteinmpnn.py sends."""
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
    #  Residues the researcher picked to hold fixed, {chain: [resi, ...]}. The
    #  design endpoint keeps these at their native identity (clients/proteinmpnn.py
    #  takes fixed_positions: dict[str, list[int]]). Conservation tiers are a
    #  separate, automatic source; an explicit pick is passed through as given.
    fixed = si.request.get("fixed_positions")
    if isinstance(fixed, dict) and fixed:
        picked = {
            str(chain): sorted({int(p) for p in positions})
            for chain, positions in fixed.items()
            if positions
        }
        if picked:
            payload["fixed_positions"] = picked
    return payload


def _soluprot(si: StageInput) -> dict[str, Any]:
    """Solubility. Every designed sequence goes in one call."""
    fasta = _require(si.text("designed_fasta", "target_fasta", "fasta"), "설계 서열")
    records = _records(fasta)
    if not records:
        raise PayloadError("FASTA 에서 서열을 찾지 못했다")
    return {"sequences": [{"id": r.id, "sequence": r.sequence} for r in records]}


def _af2(si: StageInput) -> dict[str, Any]:
    """Structure prediction. The MSA travels with it when there is one."""
    fasta = _require(si.text("designed_fasta", "target_fasta", "fasta"), "예측 대상 서열")
    records = _records(fasta)
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
    """Build the input for a model.

    A model with no builder gets the request unchanged: one someone registered
    themselves has a schema of its own and guessing at it would be worse than
    passing it through.
    """
    builder = BUILDERS.get(model_id)
    si = StageInput(request=request or {}, upstream=upstream or {})
    if builder is None:
        return dict(si.request)
    return builder(si)


def known_models() -> tuple[str, ...]:
    return tuple(sorted(BUILDERS))
