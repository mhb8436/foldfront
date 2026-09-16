import sys
import os

# Add pipeline_mcp to path
sys.path.insert(0, '/opt/protein_pipeline/pipeline-mcp/src')

from pipeline_mcp.bio.pdb import ca_rmsd

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

with open(bioemu_pdb, 'r') as f:
    bioemu_text = f.read()
    
with open(af2_pdb, 'r') as f:
    af2_text = f.read()

rmsd = ca_rmsd(bioemu_text, af2_text)
print(f"ca_rmsd: {rmsd}")

try:
    import pymol
    pymol.pymol_argv = ['pymol', '-qc']
    pymol.finish_launching()
    
    pymol.cmd.load(bioemu_pdb, "bioemu")
    pymol.cmd.load(af2_pdb, "af2")
    
    # Sequence-independent alignment
    align_result = pymol.cmd.align("bioemu", "af2")
    super_result = pymol.cmd.super("bioemu", "af2")
    
    print(f"pymol align RMSD: {align_result[0]}")
    print(f"pymol super RMSD: {super_result[0]}")
except ImportError:
    print("PyMOL not installed. Trying biopython...")
    from Bio.PDB import PDBParser, Superimposer
    
    parser = PDBParser()
    bioemu_struct = parser.get_structure("bioemu", bioemu_pdb)
    af2_struct = parser.get_structure("af2", af2_pdb)
    
    bioemu_atoms = [atom for atom in bioemu_struct.get_atoms() if atom.get_name() == 'CA']
    af2_atoms = [atom for atom in af2_struct.get_atoms() if atom.get_name() == 'CA']
    
    # Assuming same number of CA atoms for a simple superimposer
    if len(bioemu_atoms) == len(af2_atoms):
        sup = Superimposer()
        sup.set_atoms(bioemu_atoms, af2_atoms)
        print(f"Biopython Superimposer RMSD: {sup.rms}")
    else:
        print(f"Different number of CA atoms: {len(bioemu_atoms)} vs {len(af2_atoms)}")
