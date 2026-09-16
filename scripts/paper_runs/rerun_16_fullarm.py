#!/usr/bin/env python3
"""Re-run ALL 4 arms for the 4 chain-contaminated structural-context targets.

The original ablation fed multi-chain CATH PDBs with design_chains=None, so every
arm (single, rfd3_single, bioemu, rfd3_bioemu) for these targets used the wrong
chain A (or, for 1iieA00, the 20-model NMR concatenation). This re-runs the full
arm with the correct single-chain, first-model reference. Originals are backed up
first; corrected runs overwrite the standard run_ids so the analysis script
regenerates correct summary/sequences/paired-test CSVs and Figure 3.
"""
from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

PROJECT_ROOT = Path("/opt/protein_pipeline-work")
sys.path.insert(0, str(PROJECT_ROOT / "pipeline-mcp" / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "benchmark"))

from dotenv import load_dotenv
load_dotenv("/opt/protein_pipeline/pipeline-mcp/.env", override=True)
os.environ["PIPELINE_OUTPUT_ROOT"] = str(PROJECT_ROOT / "outputs")

from pipeline_mcp.app import build_runner
import backbone_ensemble_ablation as abl

SC = Path("/opt/structural_context_18_targets/pdb_single_chain")
TARGETS = {"1agrE02": "1agrE02_E.pdb", "1b12B02": "1b12B02_B.pdb",
           "2auaB01": "2auaB01_B.pdb", "1iieA00": "1iieA00_A.pdb"}
ARMS = ["single", "rfd3_single", "bioemu", "rfd3_bioemu"]
OUT = PROJECT_ROOT / "outputs"
BAK = Path("/opt/abl_wrongchain_backup")
BAK.mkdir(exist_ok=True)

def main() -> int:
    runner = build_runner()
    for target, fname in TARGETS.items():
        pdb_text = (SC / fname).read_text(encoding="utf-8")
        for arm in ARMS:
            run_id = f"abl_be_{target}_{arm}_s1"
            run_dir = OUT / run_id
            # back up original (wrong-chain) run once
            bdir = BAK / run_id
            if run_dir.exists() and not bdir.exists():
                shutil.move(str(run_dir), str(bdir))
                print(f"[backup] {run_id} -> {bdir}", flush=True)
            request = abl.build_request(pdb_text, arm, seed=1)
            request = request.__class__(**{**request.__dict__, "force": True})
            print(f"[run] {run_id}  (input {fname}, arm {arm})", flush=True)
            try:
                runner.run(request, run_id=run_id)
                # quick gate read for bioemu arms
                gp = run_dir / "bioemu" / "target_gate_summary.json"
                if gp.exists():
                    g = json.load(gp.open()); vals = list(g.get("rmsd_by_id", {}).values())
                    print(f"[gate] {run_id}: chains={g.get('design_chains')} "
                          f"acc={g.get('accepted_count')}/{g.get('input_count')} "
                          f"minRMSD={round(min(vals),3) if vals else None}", flush=True)
            except Exception as exc:
                print(f"[error] {run_id}: {exc}", file=sys.stderr, flush=True)
    print("\n=== ALL 16 RUNS DONE ===", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
