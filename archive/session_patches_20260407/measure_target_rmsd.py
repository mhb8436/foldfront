import sys, json
sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import ca_rmsd

wt_pdb = open("./outputs/admin_20260325_061633_2249cbdc/target.original.pdb").read()
bb_json = json.load(open("./outputs/admin_20260325_061633_2249cbdc/backbones.json"))

print("--- BioEmu ---")
for b in bb_json["backbones"]:
    if "bioemu" in b["id"] or "sample_" in b["id"]:
        pdb = b.get("pdb_text") or b.get("pdb")
        if pdb:
            val = ca_rmsd(wt_pdb, pdb, chains=["A"])
            print(f"{b['id']}: {val:.3f} A")

print("\n--- RFD3 ---")
for b in bb_json["backbones"]:
    if "rfd3" in b["id"]:
        pdb = b.get("pdb_text") or b.get("pdb")
        if pdb:
            val = ca_rmsd(wt_pdb, pdb, chains=["A"])
            print(f"{b['id']}: {val:.3f} A")
