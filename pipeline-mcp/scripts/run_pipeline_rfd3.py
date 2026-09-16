from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def _env_true(name: str) -> bool:
    return str(os.environ.get(name, "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_base_url() -> str:
    for key in ("PIPELINE_MCP_HTTP_URL", "PIPELINE_MCP_BASE_URL", "PIPELINE_HTTP_URL"):
        value = str(os.environ.get(key, "") or "").strip()
        if value:
            return value
    return "http://127.0.0.1:8000"


def _default_run_id(prefix: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}"


def _parse_floats(raw: str) -> list[float]:
    items: list[float] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        items.append(float(part))
    return items


@dataclass(frozen=True)
class PipelineStatus:
    found: bool
    state: str | None
    stage: str | None
    detail: str | None
    raw: dict[str, Any]


class PipelineClient:
    def __init__(self, base_url: str, *, timeout_s: float = 60.0) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = float(timeout_s)
        self.session = requests.Session()

    def _call(self, name: str, arguments: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        timeout = float(timeout_s if timeout_s is not None else self.timeout_s)
        resp = self.session.post(
            f"{self.base_url}/tools/call",
            json={"name": name, "arguments": arguments},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok", False):
            raise RuntimeError(payload.get("error") or f"{name} failed")
        return payload.get("result") or {}

    def status(self, run_id: str) -> PipelineStatus:
        result = self._call("pipeline.status", {"run_id": run_id}, timeout_s=30)
        found = bool(result.get("found"))
        if not found:
            return PipelineStatus(found=False, state=None, stage=None, detail=None, raw=result)
        status = result.get("status") or {}
        return PipelineStatus(
            found=True,
            state=str(status.get("state") or "") or None,
            stage=str(status.get("stage") or "") or None,
            detail=str(status.get("detail") or "") or None,
            raw=result,
        )

    def run(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        return self._call("pipeline.run", payload, timeout_s=timeout_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full protein pipeline with RFD3 pre-stage.")
    parser.add_argument("--base-url", default=_default_base_url(), help="pipeline-mcp base URL")
    parser.add_argument("--run-id", default="", help="run_id to use (optional)")
    parser.add_argument("--run-id-prefix", default="rfd3_full", help="prefix for auto run_id")
    parser.add_argument("--reuse", action="store_true", help="reuse existing run_id if found (do not auto-rename)")
    parser.add_argument("--poll-interval", type=int, default=60, help="seconds between status polls")
    parser.add_argument("--run-timeout", type=int, default=600, help="pipeline.run HTTP timeout (seconds)")

    parser.add_argument("--rfd3-input-pdb", required=True, help="path to input PDB file")
    parser.add_argument("--rfd3-contig", default="", help="RFD3 contig, e.g. A1-229 (optional if using inputs)")
    parser.add_argument(
        "--rfd3-mode",
        default="",
        help="RFD3 mode: legacy_contig, binder, enzyme, local_diversify, or advanced",
    )
    parser.add_argument("--rfd3-inputs-path", default="", help="path to RFD3 inputs.json/yaml (optional)")
    parser.add_argument("--rfd3-inputs-text", default="", help="inline RFD3 inputs JSON/YAML (optional)")
    parser.add_argument("--rfd3-design-index", type=int, default=0)
    parser.add_argument("--rfd3-max-return-designs", type=int, default=50)
    parser.add_argument("--rfd3-partial-t", type=float, default=None)
    parser.add_argument(
        "--rfd3-sampling-strategy",
        default="",
        help="RFD3 sampling strategy: auto, batch, or independent_jobs",
    )
    parser.add_argument(
        "--rfd3-fail-on-duplicate-backbones",
        action="store_true",
        help="fail if RFD3 still collapses to fewer unique backbones than requested",
    )
    parser.add_argument("--rfd3-use-ensemble", action="store_true")
    parser.add_argument("--rfd3-cli-args", default="", help="extra RFD3 CLI args (optional)")

    parser.add_argument("--conservation-tiers", default="0.5", help="comma-separated tiers, e.g. 0.3,0.5,0.7")
    parser.add_argument("--num-seq-per-tier", type=int, default=2)
    parser.add_argument("--mmseqs-max-seqs", type=int, default=200)
    parser.add_argument(
        "--mmseqs-use-gpu",
        action="store_true",
        default=_env_true("PIPELINE_MMSEQS_USE_GPU") or _env_true("MMSEQS_USE_GPU"),
        help="use GPU for MMseqs",
    )
    parser.add_argument("--af2-top-k", type=int, default=1)

    args = parser.parse_args()

    run_id = str(args.run_id or "").strip() or _default_run_id(args.run_id_prefix)
    base_url = str(args.base_url).strip()

    pdb_path = Path(args.rfd3_input_pdb)
    if not pdb_path.exists():
        raise SystemExit(f"Missing PDB: {pdb_path}")
    pdb_text = pdb_path.read_text(encoding="utf-8", errors="replace")
    inputs_text = str(args.rfd3_inputs_text or "").strip()
    if not inputs_text and str(args.rfd3_inputs_path or "").strip():
        inputs_path = Path(args.rfd3_inputs_path)
        if not inputs_path.exists():
            raise SystemExit(f"Missing inputs file: {inputs_path}")
        inputs_text = inputs_path.read_text(encoding="utf-8", errors="replace").strip()
    contig = str(args.rfd3_contig or "").strip()
    if not inputs_text and not contig:
        raise SystemExit("Provide --rfd3-contig or --rfd3-inputs-path/--rfd3-inputs-text")

    client = PipelineClient(base_url)

    status = client.status(run_id)
    if status.found:
        state = (status.state or "").lower()
        print(f"existing run_id={run_id} state={state} stage={status.stage} detail={status.detail}")
        if state == "running":
            for i in range(240):
                time.sleep(int(args.poll_interval))
                status = client.status(run_id)
                state = (status.state or "").lower()
                print(f"poll {i}: state={state} stage={status.stage} detail={status.detail}")
                if state and state != "running":
                    print(json.dumps(status.raw, ensure_ascii=False, indent=2)[:4000])
                    return
            raise SystemExit("Run still running; stop polling.")
        if not args.reuse:
            raise SystemExit(
                f"run_id={run_id} already exists. Use --reuse to continue or choose a new --run-id."
            )

    payload = {
        "run_id": run_id,
        "rfd3_input_pdb": pdb_text,
        "rfd3_mode": str(args.rfd3_mode or "").strip() or None,
        "rfd3_contig": contig or None,
        "rfd3_inputs_text": inputs_text or None,
        "rfd3_design_index": int(args.rfd3_design_index),
        "rfd3_max_return_designs": int(args.rfd3_max_return_designs),
        "rfd3_partial_t": (float(args.rfd3_partial_t) if args.rfd3_partial_t is not None else None),
        "rfd3_sampling_strategy": str(args.rfd3_sampling_strategy or "").strip() or None,
        "rfd3_fail_on_duplicate_backbones": bool(args.rfd3_fail_on_duplicate_backbones),
        "rfd3_use_ensemble": bool(args.rfd3_use_ensemble),
        "rfd3_cli_args": str(args.rfd3_cli_args or "").strip() or None,
        "conservation_tiers": _parse_floats(args.conservation_tiers),
        "num_seq_per_tier": int(args.num_seq_per_tier),
        "mmseqs_max_seqs": int(args.mmseqs_max_seqs),
        "mmseqs_use_gpu": bool(args.mmseqs_use_gpu),
        "af2_top_k": int(args.af2_top_k),
    }

    print(f"starting pipeline.run run_id={run_id}")
    try:
        result = client.run(payload, timeout_s=int(args.run_timeout))
        print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
        return
    except requests.exceptions.Timeout:
        print("pipeline.run timed out; polling status...")

    for i in range(240):
        time.sleep(int(args.poll_interval))
        status = client.status(run_id)
        state = (status.state or "").lower()
        print(f"poll {i}: state={state} stage={status.stage} detail={status.detail}")
        if state and state != "running":
            print(json.dumps(status.raw, ensure_ascii=False, indent=2)[:4000])
            return

    raise SystemExit("Run still running; stop polling.")


if __name__ == "__main__":
    main()
