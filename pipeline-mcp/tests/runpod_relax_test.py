import sys
import os
from pathlib import Path

# 1. Load .env explicitly for the test script
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

def test_runpod_relax():
    endpoint_id = os.getenv("RUNPOD_RELAX_ENDPOINT_ID")
    api_key = os.getenv("RUNPOD_API_KEY")
    
    print(f"--- RunPod Relax Test ---")
    print(f"Endpoint ID: {endpoint_id}")
    print(f"API Key present: {bool(api_key)}")
    
    if not endpoint_id or "발급받은" in endpoint_id:
        print("❌ Error: Valid RUNPOD_RELAX_ENDPOINT_ID is required.")
        return
    if not api_key:
        print("❌ Error: Valid RUNPOD_API_KEY is required.")
        return

    client = RosettaRelaxClient()
    
    # 2. Create a minimal valid PDB file for Rosetta
    # (Just 3 residues so it doesn't crash the parser immediately)
    dummy_pdb = "outputs/test_relax_input.pdb"
    os.makedirs("outputs", exist_ok=True)
    with open(dummy_pdb, "w") as f:
        f.write("ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\n")
        f.write("ATOM      2  CA  GLY A   1       1.458   0.000   0.000  1.00  0.00           C\n")
        f.write("ATOM      3  C   GLY A   1       2.009   1.420   0.000  1.00  0.00           C\n")
        f.write("ATOM      4  O   GLY A   1       1.218   2.356   0.000  1.00  0.00           O\n")
        f.write("ATOM      5  N   ALA A   2       3.336   1.579   0.000  1.00  0.00           N\n")
        f.write("ATOM      6  CA  ALA A   2       3.987   2.894   0.000  1.00  0.00           C\n")
        f.write("ATOM      7  C   ALA A   2       5.502   2.665   0.000  1.00  0.00           C\n")
        f.write("ATOM      8  O   ALA A   2       6.223   3.639   0.000  1.00  0.00           O\n")
        f.write("ATOM      9  CB  ALA A   2       3.421   3.642  -1.196  1.00  0.00           C\n")
        f.write("TER      10      ALA A   2\n")
        f.write("END\n")

    output_dir = Path("outputs/test_relax_out")
    output_dir.mkdir(exist_ok=True)
    
    try:
        print("\n🚀 Sending request to RunPod Serverless...")
        result = client.run(Path(dummy_pdb), output_dir)
        print("\n✅ RunPod Relax Response Received Successfully!")
        print(f" - Returned Total Score: {result.get('best_score')}")
        print(f" - Score per residue: {result.get('score_per_residue')}")
        print(f" - Relaxed PDB saved at: {result.get('best_pdb')}")
        
        if result.get('best_pdb') and os.path.exists(result.get('best_pdb')):
             print(" - File creation check: SUCCESS")
             
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")
    finally:
        if os.path.exists(dummy_pdb): os.remove(dummy_pdb)

if __name__ == "__main__":
    test_runpod_relax()
