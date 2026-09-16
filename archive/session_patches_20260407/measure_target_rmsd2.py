import sys, os, glob
sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import ca_rmsd

wt_pdb = open("./outputs/admin_20260325_061633_2249cbdc/target.original.pdb").read()

print("--- BioEmu ---")
for p in sorted(glob.glob("./outputs/admin_20260325_061633_2249cbdc/backbones/sample_*/target.pdb"))[:5]:
    val = ca_rmsd(wt_pdb, open(p).read(), chains=["A"])
    print(f"{os.path.basename(os.path.dirname(p))}: {val:.3f} A" if val else "None")

print("\n--- RFD3 ---")
for p in sorted(glob.glob("./outputs/admin_20260325_061633_2249cbdc/backbones/rfd3_*/target.pdb"))[:5]:
    val = ca_rmsd(wt_pdb, open(p).read(), chains=["A"])
    print(f"{os.path.basename(os.path.dirname(p))}: {val:.3f} A" if val else "None")
