import sys
import os
from pathlib import Path

env_path = Path("pipeline-mcp/.env")
if env_path.exists():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value

sys.path.append(os.path.abspath('pipeline-mcp/src'))
from pipeline_mcp.clients.rosetta_relax import RosettaRelaxClient

def test_runpod_relax_real():
    client = RosettaRelaxClient()
    
    # Use a REAL PDB file from previous outputs
    real_pdb_path = Path("outputs/admin_no_ensemble/target.original.pdb")
    if not real_pdb_path.exists():
         print(f"❌ Real PDB not found at {real_pdb_path}")
         return
         
    output_dir = Path("outputs/test_relax_real_out")
    output_dir.mkdir(exist_ok=True)
    
    try:
        print(f"\n🚀 Sending REAL PDB ({real_pdb_path}) to RunPod...")
        result = client.run(real_pdb_path, output_dir)
        print("\n✅ RunPod Relax Response Received Successfully!")
        print(f" - Returned Total Score: {result.get('best_score')}")
        print(f" - Score per residue: {result.get('score_per_residue')}")
        print(f" - Relaxed PDB saved at: {result.get('best_pdb')}")
             
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")

if __name__ == "__main__":
    test_runpod_relax_real()
