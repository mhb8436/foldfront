#!/usr/bin/env python3
"""Trimmed chain-corrected re-run: only the arms whose corrected data actually
feeds the reported 18-target ablation statistics.

- single + rfd3_single: all 4 contaminated targets (evaluable for all 18, so they
  contribute to the single/RFD3 arm distributions and paired tests).
- bioemu + rfd3_bioemu: only 1agrE02, 1b12B02 (these PASS the gate on the correct
  chain -> evaluable design metrics needed). 2auaB01 and 1iieA00 fail the gate even
  with the correct chain (already confirmed), so their BioEmu / RFD3+BioEmu arms
  produce no evaluable design data -> re-running them is pointless and skipped.

Skips runs already corrected and completed; backs up originals once.
"""
from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

PR = Path("/opt/protein_pipeline-work")
sys.path.insert(0, str(PR / "pipeline-mcp" / "src"))
sys.path.insert(0, str(PR / "scripts" / "benchmark"))
from dotenv import load_dotenv
load_dotenv("/opt/protein_pipeline/pipeline-mcp/.env", override=True)
os.environ["PIPELINE_OUTPUT_ROOT"] = str(PR / "outputs")
from pipeline_mcp.app import build_runner
import backbone_ensemble_ablation as abl

SC = Path("/opt/structural_context_18_targets/pdb_single_chain")
PDB = {"1agrE02": "1agrE02_E.pdb", "1b12B02": "1b12B02_B.pdb",
       "2auaB01": "2auaB01_B.pdb", "1iieA00": "1iieA00_A.pdb"}
PLAN = {
    "1agrE02": ["single", "rfd3_single", "bioemu", "rfd3_bioemu"],
    "1b12B02": ["single", "rfd3_single", "bioemu", "rfd3_bioemu"],
    "2auaB01": ["single", "rfd3_single"],
    "1iieA00": ["single", "rfd3_single"],
}
OUT = PR / "outputs"; BAK = Path("/opt/abl_wrongchain_backup"); BAK.mkdir(exist_ok=True)

def status(rd):
    try: d = json.load((rd / "status.json").open()); return d.get("stage"), d.get("state")
    except Exception: return None, None

def main():
    runner = build_runner()
    todo = [(t, a) for t, arms in PLAN.items() for a in arms]
    print(f"plan: {len(todo)} arm-runs", flush=True)
    for t, arm in todo:
        run_id = f"abl_be_{t}_{arm}_s1"; rd = OUT / run_id; bd = BAK / run_id
        stage, state = status(rd)
        if bd.exists() and stage == "done" and state == "completed":
            print(f"[skip] {run_id}: already corrected & completed", flush=True); continue
        if not bd.exists() and rd.exists():
            shutil.move(str(rd), str(bd)); print(f"[backup] {run_id}", flush=True)
        req = abl.build_request((SC / PDB[t]).read_text(), arm, seed=1)
        req = req.__class__(**{**req.__dict__, "force": True})
        print(f"[run] {run_id} (chain-corrected, arm {arm})", flush=True)
        try:
            runner.run(req, run_id=run_id)
            gp = rd / "bioemu" / "target_gate_summary.json"
            if gp.exists():
                g = json.load(gp.open()); v = list(g.get("rmsd_by_id", {}).values())
                print(f"[gate] {run_id}: acc={g.get('accepted_count')}/{g.get('input_count')} "
                      f"minRMSD={round(min(v),3) if v else None}", flush=True)
        except Exception as exc:
            print(f"[error] {run_id}: {exc}", file=sys.stderr, flush=True)
    print("=== TRIMMED RE-RUN DONE ===", flush=True)

if __name__ == "__main__":
    raise SystemExit(main())
