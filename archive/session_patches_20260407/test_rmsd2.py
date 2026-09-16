import sys
sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import ca_rmsd

wt_pdb = open("./outputs/admin_20260325_061633_2249cbdc/target.original.pdb").read()
rfd3_pdb = open("./outputs/admin_20260325_061633_2249cbdc/backbones/rfd3_spec-1_0_model_5/target.pdb").read()

for c in ["A", "B", "C", "D", "E"]:
    val = ca_rmsd(wt_pdb, rfd3_pdb, chains=[c])
    print(f"Chain {c} RMSD: {val}")
