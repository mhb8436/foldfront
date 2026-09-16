import pymol
pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")

aln = pymol.cmd.align("bioemu and name CA", "af2 and name CA", object="aln_obj")
print(f"align RMSD: {aln[0]} over {aln[1]} atoms")

# Let's get the mapping
mapping = pymol.cmd.get_raw_alignment("aln_obj")

# Get atom to residue mapping
bioemu_atoms = pymol.cmd.get_model("bioemu").atom
af2_atoms = pymol.cmd.get_model("af2").atom

bioemu_idx_to_res = {a.index: a.resi for a in bioemu_atoms}
af2_idx_to_res = {a.index: a.resi for a in af2_atoms}

for idx, (m1, m2) in enumerate(mapping):
    # m1 is af2, m2 is bioemu
    if idx < 10 or idx > len(mapping) - 10:
        print(f"AF2 res {af2_idx_to_res[m1[1]]} <-> BioEmu res {bioemu_idx_to_res[m2[1]]}")
