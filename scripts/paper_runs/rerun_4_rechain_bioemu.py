#!/usr/bin/env python3
"""Re-run the BioEmu arm for the 4 mis-extracted structural-context targets.

Original ablation fed multi-chain CATH PDBs with design_chains=None, so the
pipeline defaulted to chain A -- wrong for targets whose CATH domain is on
chain E/B (1agrE02, 1b12B02, 2auaB01) -- and concatenated all 20 NMR models
for 1iieA00 (75 aa x 20 = 1500 aa -> BioEmu timeout). This re-runs BioEmu with
the correct single-chain, first-model reference to test whether the 2.0 A
target-RMSD gate actually fails on genuine input. stop_after='bioemu' so we get
the gate verdict without the downstream design/AF2 cost.
"""
from __future__ import annotations
import json, os, sys
from dataclasses import replace
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
TARGETS = {
    "1agrE02": "1agrE02_E.pdb",
    "1b12B02": "1b12B02_B.pdb",
    "2auaB01": "2auaB01_B.pdb",
    "1iieA00": "1iieA00_A.pdb",
}

def main() -> int:
    runner = build_runner()
    results = {}
    for target, fname in TARGETS.items():
        pdb_text = (SC / fname).read_text(encoding="utf-8")
        request = replace(
            abl.build_request(pdb_text, "bioemu", seed=1),
            stop_after="bioemu",
            force=True,
        )
        run_id = f"abl_be_{target}_bioemu_s1_rechain"
        print(f"[run] {run_id}  (input {fname})", flush=True)
        try:
            runner.run(request, run_id=run_id)
        except Exception as exc:
            print(f"[error] {run_id}: {exc}", file=sys.stderr, flush=True)
        # read gate summary
        gp = PROJECT_ROOT / "outputs" / run_id / "bioemu" / "target_gate_summary.json"
        if gp.exists():
            g = json.load(gp.open())
            vals = list(g.get("rmsd_by_id", {}).values())
            results[target] = {
                "design_chains": g.get("design_chains"),
                "accepted": g.get("accepted_count"),
                "input": g.get("input_count"),
                "min_rmsd": round(min(vals), 3) if vals else None,
            }
            print(f"[gate] {target}: {results[target]}", flush=True)
        else:
            results[target] = {"error": "no gate summary"}
            print(f"[gate] {target}: no summary", flush=True)
    print("\n=== SUMMARY ===")
    print(json.dumps(results, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
