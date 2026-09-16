import sys, os, glob

sys.path.insert(0, "./pipeline-mcp/src")
from pipeline_mcp.bio.pdb import ca_rmsd, dssp_non_loop_positions_by_chain

wt_pdb_path = "1LVM_no_neg.pdb"
if not os.path.exists(wt_pdb_path):
    wt_pdb_path = "./outputs/admin_20260325_061633_2249cbdc/target.original.pdb"

with open(wt_pdb_path) as f:
    wt_pdb = f.read()

print(f"Loaded input PDB: {wt_pdb_path}")

dssp_positions = dssp_non_loop_positions_by_chain(wt_pdb)
num_residues = sum(len(v) for v in dssp_positions.values())
print(f"DSSP non-loop residues identified: {num_residues}")

print("\n--- BioEmu Backbone DSSP CA-RMSD ---")
bioemu_pdbs = sorted(
    glob.glob("./outputs/admin_20260325_061633_2249cbdc/backbones/sample_*/target.pdb")
)[:5]
for p in bioemu_pdbs:
    with open(p) as f:
        mobile_pdb = f.read()
    val = ca_rmsd(wt_pdb, mobile_pdb, include_positions=dssp_positions)
    print(
        f"{os.path.basename(os.path.dirname(p))}: {val:.3f} A"
        if val is not None
        else f"{p}: None"
    )

print("\n--- RFD3 Backbone DSSP CA-RMSD (Old Run) ---")
rfd3_old_pdbs = sorted(
    glob.glob("./outputs/admin_20260325_061633_2249cbdc/backbones/rfd3_*/target.pdb")
)[:5]
for p in rfd3_old_pdbs:
    with open(p) as f:
        mobile_pdb = f.read()
    val = ca_rmsd(wt_pdb, mobile_pdb, include_positions=dssp_positions)
    print(
        f"{os.path.basename(os.path.dirname(p))}: {val:.3f} A"
        if val is not None
        else f"{p}: None"
    )

print("\n--- RFD3 Backbone DSSP CA-RMSD (Fixed New Run) ---")
rfd3_new_pdbs = sorted(glob.glob("./outputs/rfd3_1lvm_20260406_095518/designs/*.pdb"))[
    :5
]
for p in rfd3_new_pdbs:
    with open(p) as f:
        mobile_pdb = f.read()
    val = ca_rmsd(wt_pdb, mobile_pdb, include_positions=dssp_positions)
    print(f"{os.path.basename(p)}: {val:.3f} A" if val is not None else f"{p}: None")
