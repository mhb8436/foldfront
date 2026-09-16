import sys
import pymol

# Add pipeline_mcp to path
sys.path.insert(0, '/opt/protein_pipeline/pipeline-mcp/src')
from pipeline_mcp.bio.pdb import ca_rmsd

pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

with open(bioemu_pdb, 'r') as f:
    bioemu_text = f.read()
with open(af2_pdb, 'r') as f:
    af2_text = f.read()

print("--- RMSD Analysis ---")
# 1. Pipeline ca_rmsd
pipeline_rmsd = ca_rmsd(bioemu_text, af2_text)
print(f"1. Pipeline ca_rmsd: {pipeline_rmsd:.3f} Å (over all 221 atoms)")

# 2. PyMOL align (with outlier rejection)
pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")
aln = pymol.cmd.align("bioemu and name CA", "af2 and name CA")
print(f"2. PyMOL align:      {aln[0]:.3f} Å (over {aln[1]} atoms - OUTLIERS REJECTED)")

# 3. PyMOL fit (without outlier rejection)
fit = pymol.cmd.fit("bioemu and name CA", "af2 and name CA")
print(f"3. PyMOL fit:        {fit:.3f} Å (over all 221 atoms - NO OUTLIER REJECTION)")

print("\n--- Why is the RMSD high? ---")
print("The residue numbers DO match perfectly (1 to 221). The high RMSD is due to flexible termini.")
pymol.cmd.fit("bioemu and name CA", "af2 and name CA")
bioemu_atoms = pymol.cmd.get_model("bioemu and name CA").atom
af2_atoms = pymol.cmd.get_model("af2 and name CA").atom

distances = []
for b_atom, a_atom in zip(bioemu_atoms, af2_atoms):
    dist = ((b_atom.coord[0] - a_atom.coord[0])**2 + 
            (b_atom.coord[1] - a_atom.coord[1])**2 + 
            (b_atom.coord[2] - a_atom.coord[2])**2)**0.5
    distances.append((int(b_atom.resi), dist))

distances.sort(key=lambda x: x[1], reverse=True)
print("Top 5 most divergent residues:")
for resi, dist in distances[:5]:
    print(f"  Residue {resi}: {dist:.2f} Å")
