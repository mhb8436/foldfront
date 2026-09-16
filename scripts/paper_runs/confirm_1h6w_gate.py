#!/usr/bin/env python3
"""Empirical check: does 1h6wA03 (a 'genuine' BioEmu failure, correct chain) still
fail the 2.0 A target-RMSD gate when re-run from the clean single-chain reference?
If RMSD stays high (~20 A), the failure is genuine fold divergence, not a
numbering/alignment artifact (the gate already does Kabsch superposition and
renumbers both reference and samples from 1)."""
from __future__ import annotations
import json, os, sys
from dataclasses import replace
from pathlib import Path

PR = Path("/opt/protein_pipeline-work")
sys.path.insert(0, str(PR / "pipeline-mcp" / "src"))
sys.path.insert(0, str(PR / "scripts" / "benchmark"))
from dotenv import load_dotenv
load_dotenv("/opt/protein_pipeline/pipeline-mcp/.env", override=True)
os.environ["PIPELINE_OUTPUT_ROOT"] = str(PR / "outputs")
from pipeline_mcp.app import build_runner
import backbone_ensemble_ablation as abl

SC = Path("/opt/structural_context_18_targets/pdb_single_chain/1h6wA03_A.pdb")
run_id = "abl_be_1h6wA03_bioemu_s1_confirm"

def main() -> int:
    runner = build_runner()
    req = replace(abl.build_request(SC.read_text(), "bioemu", seed=1), stop_after="bioemu", force=True)
    print(f"[run] {run_id} (clean single-chain 1h6wA03_A)", flush=True)
    try:
        runner.run(req, run_id=run_id)
    except Exception as exc:
        print(f"[error] {run_id}: {exc}", file=sys.stderr, flush=True)
    gp = PR / "outputs" / run_id / "bioemu" / "target_gate_summary.json"
    if gp.exists():
        g = json.load(gp.open()); vals = list(g.get("rmsd_by_id", {}).values())
        print(f"[gate] 1h6wA03 confirm: chains={g.get('design_chains')} "
              f"acc={g.get('accepted_count')}/{g.get('input_count')} "
              f"minRMSD={round(min(vals),3) if vals else None} "
              f"all={[round(v,1) for v in vals]}", flush=True)
    print("DONE", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
