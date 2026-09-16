import pymol
pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")

# Calculate RMSD without outlier rejection
rms = pymol.cmd.rms_cur("bioemu and name CA", "af2 and name CA")
print(f"rms_cur (no alignment): {rms}")

# Align and calculate RMSD over all atoms
fit = pymol.cmd.fit("bioemu and name CA", "af2 and name CA")
print(f"fit (alignment, no outlier rejection): {fit}")

