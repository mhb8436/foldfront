import pymol
pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")

# 1. PyMOL align (with outlier rejection)
aln = pymol.cmd.align("bioemu and name CA", "af2 and name CA")
print(f"PyMOL align (with outlier rejection): RMSD = {aln[0]:.3f} over {aln[1]} atoms")

# 2. PyMOL fit (without outlier rejection, aligns all matching atoms)
fit = pymol.cmd.fit("bioemu and name CA", "af2 and name CA")
print(f"PyMOL fit (without outlier rejection): RMSD = {fit:.3f} over 221 atoms")

# 3. Pipeline ca_rmsd
import sys
sys.path.insert(0, '/opt/protein_pipeline/pipeline-mcp/src')
from pipeline_mcp.bio.pdb import ca_rmsd

with open(bioemu_pdb, 'r') as f:
    bioemu_text = f.read()
with open(af2_pdb, 'r') as f:
    af2_text = f.read()

pipeline_rmsd = ca_rmsd(bioemu_text, af2_text)
print(f"Pipeline ca_rmsd: RMSD = {pipeline_rmsd:.3f} over 221 atoms")
