import pymol
pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")

# align returns (RMSD, num_atoms, 5, 0, 0)
aln = pymol.cmd.align("bioemu", "af2", object="aln_obj")
print(f"align RMSD: {aln[0]} over {aln[1]} atoms")

# Let's get the mapping
mapping = pymol.cmd.get_raw_alignment("aln_obj")
for idx, (m1, m2) in enumerate(mapping):
    if idx < 10 or idx > len(mapping) - 10:
        print(m1, m2)
