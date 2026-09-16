import sys, json
sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import _ca_coords_by_chain, _match_ca_coords

wt_pdb = open("./outputs/admin_20260325_061633_2249cbdc/target.original.pdb").read()
bb_json = json.load(open("./outputs/admin_20260325_061633_2249cbdc/backbones.json"))
b = [x for x in bb_json["backbones"] if x["id"] == "sample_0003"][0]
pdb = b.get("pdb_text") or b.get("pdb")

ref = _ca_coords_by_chain(wt_pdb, chains=["A"])
mob = _ca_coords_by_chain(pdb, chains=["A"])
matched_ref, matched_mob = _match_ca_coords(ref, mob, chains=["A"])
print(f"Ref A len: {len(ref['A'])}, Mob A len: {len(mob['A'])}, Matched: {len(matched_ref)}")

# Dump first 5 matched
for r, m in zip(list(ref['A'].keys())[:5], list(mob['A'].keys())[:5]):
    print(r, m)
