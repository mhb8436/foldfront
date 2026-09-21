#!/usr/bin/env python3
"""One real call to one real endpoint, end to end.

    PROTEINMPNN_ENDPOINT_ID=xxxx uv run python tools/gpu_smoke.py

Everything until now has been the mock adapter, and mock numbers are not
evidence. This walks the whole path with a real model instead:

    payload builder -> router -> RunPod adapter -> endpoint
                                                      |
    quality signals <- result interpreter <-----------+

and prints what came back at each step, so a failure names the step rather
than the run. Nothing is written to the database - this is a check on the
path, not a run of the platform. `foldfront.cli demo` is for that.

**It costs money.** One call on the cheapest GPU is cents, most of it the
cold start while the image is pulled. The endpoint should be created with
`workersMin: 0` so nothing bills between calls.

The input is a real backbone from the original's case studies rather than a
synthetic one, because a model that rejects a malformed PDB and a model that
is unreachable fail in ways worth telling apart.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform" / "src"))
sys.path.insert(0, str(ROOT / "pipeline-mcp" / "src"))

#  Which endpoint variable feeds which model, and what a reply should carry.
TARGETS: dict[str, tuple[str, str]] = {
    "PROTEINMPNN_ENDPOINT_ID": ("proteinmpnn", "설계 서열"),
    "MMSEQS_ENDPOINT_ID": ("msa", "정렬"),
    "RFD3_ENDPOINT_ID": ("rfd3", "백본"),
    "COLABFOLD_ENDPOINT_ID": ("colabfold", "구조"),
    "AF2_ENDPOINT_ID": ("af2", "구조"),
    "BIOEMU_ENDPOINT_ID": ("bioemu", "앙상블"),
    "DIFFDOCK_ENDPOINT_ID": ("diffdock", "도킹"),
}


def _backbone() -> str:
    """A real PDB from the original's case studies."""
    for case in ("1lvm", "3rgk"):
        p = ROOT / "public_data" / "case_studies" / "multiround" / case / "best_design.pdb"
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise SystemExit("사례연구 PDB 를 찾지 못했습니다 — public_data/case_studies/multiround/")


def _brief(value: object, width: int = 300) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= width else text[:width] + f"… ({len(text)}자)"


async def main() -> int:
    from foldfront.engine.adapters import AdapterError, RunPodAdapter
    from foldfront.engine.payloads import StageInput, build_payload
    from foldfront.engine.results import interpret
    from foldfront.engine.router import Route
    from foldfront.engine.signals import read_signals
    from foldfront.db.models import Run, RunStatus, StageState

    key = str(os.environ.get("RUNPOD_API_KEY") or "").strip()
    if not key:
        print("RUNPOD_API_KEY 가 없습니다.")
        return 2

    picked = [(v, *TARGETS[v]) for v in TARGETS if str(os.environ.get(v) or "").strip()]
    if not picked:
        print("엔드포인트가 하나도 설정되지 않았습니다. 아래 중 하나를 .env 에 넣으십시오:")
        for var, (model, _) in TARGETS.items():
            print(f"  {var:<28} {model}")
        return 2

    pdb = _backbone()
    request = {
        "target_pdb": pdb,
        "backbone_pdb": pdb,
        "target_fasta": ">query\nMKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ\n",
        "design_chains": ["A"],
        #  Smallest useful run. Every extra sequence is more GPU seconds.
        "num_seq_per_target": 2,
        "num_designs": 1,
    }

    failures = 0
    for var, model_id, expect in picked:
        endpoint = os.environ[var].strip()
        print(f"\n{'━' * 66}\n{model_id}  ·  {var}={endpoint}")

        try:
            payload = build_payload(model_id, dict(request), None)
        except Exception as exc:
            print(f"  ✗ 입력 구성 실패 — {type(exc).__name__}: {exc}")
            failures += 1
            continue
        print(f"  입력 구성  {sorted(payload)}")

        route = Route(model_id=model_id, version="v1", transport="runpod",
                      target=endpoint, resources=None, timeout_seconds=900)
        began = time.monotonic()
        try:
            reply = await RunPodAdapter(api_key=key, timeout=900).invoke(route, payload)
        except AdapterError as exc:
            print(f"  ✗ 호출 실패 ({time.monotonic() - began:.1f}초) — {exc}")
            failures += 1
            continue
        took = time.monotonic() - began
        print(f"  ✓ 응답      {took:.1f}초 · 키 {sorted(reply)[:8]}")

        metrics = interpret(model_id, reply)
        shown = {k: v for k, v in metrics.items()
                 if not k.startswith("_") and not isinstance(v, (dict, list))
                 and not (isinstance(v, str) and len(v) > 80)}
        print(f"  지표        {_brief(shown)}")
        if metrics.get("_mock"):
            print("  ⚠ 모의 어댑터 값입니다 — 실증이 아닙니다")
            failures += 1

        run = Run(run_id="smoke", status=RunStatus.SUCCEEDED, stages=[
            StageState(name=model_id, status=RunStatus.SUCCEEDED,
                       model_id=model_id, metrics=dict(metrics)),
        ])
        for sig in read_signals(run):
            print(f"  품질 신호   [{sig.level}] {sig.message}")

        #  What the requirement actually asks for from this stage. Checked
        #  against the metric the interpreter produces, not against "did
        #  anything come back" - the first run of this said 예 for a reply
        #  that was `{"output": null}`, which is the opposite of the truth.
        EXPECTED_METRIC = {
            "proteinmpnn": "sequences", "design": "sequences",
            "msa": "depth", "rfd3": "backbones", "bioemu": "structures",
            "af2": "plddt", "colabfold": "plddt", "diffdock": "poses",
        }
        wanted = EXPECTED_METRIC.get(model_id)
        got = metrics.get(wanted) if wanted else None
        if got is None:
            print(f"  ✗ {expect} 를 받지 못했습니다 — 지표 `{wanted}` 가 없습니다")
            failures += 1
        else:
            print(f"  ✓ {expect}: {wanted}={got}")

    print(f"\n{'━' * 66}")
    print(f"{len(picked)}종 시도 · 실패 {failures}건")
    print("이 결과는 제안사 자체 측정이며 발주기관 검증이 아닙니다.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
