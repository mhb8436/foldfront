import sys, glob
sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import _ca_coords_by_chain, _match_ca_coords, ca_rmsd

wt_pdb = open("./outputs/admin_20260325_061633_2249cbdc/target.original.pdb").read()
pdb = open("./outputs/admin_20260325_061633_2249cbdc/backbones/sample_0003/target.pdb").read()

ref = _ca_coords_by_chain(wt_pdb, chains=["A"])
mob = _ca_coords_by_chain(pdb, chains=["A"])
matched_ref, matched_mob = _match_ca_coords(ref, mob, chains=["A"])
print(f"Ref A len: {len(ref['A'])}, Mob A len: {len(mob['A'])}, Matched: {len(matched_ref)}")

# Dump first 5 matched keys
for k_ref, k_mob in zip(list(ref['A'].keys())[:5], list(mob['A'].keys())[:5]):
    print("Ref", k_ref, "Mob", k_mob)

print("RMSD:", ca_rmsd(wt_pdb, pdb, chains=["A"]))
