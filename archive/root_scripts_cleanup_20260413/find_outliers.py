import pymol
pymol.pymol_argv = ['pymol', '-qc']
pymol.finish_launching()

bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

pymol.cmd.load(bioemu_pdb, "bioemu")
pymol.cmd.load(af2_pdb, "af2")

# Align all atoms to get the best fit
pymol.cmd.fit("bioemu and name CA", "af2 and name CA")

# Calculate distances between corresponding CA atoms
bioemu_atoms = pymol.cmd.get_model("bioemu and name CA").atom
af2_atoms = pymol.cmd.get_model("af2 and name CA").atom

distances = []
for b_atom, a_atom in zip(bioemu_atoms, af2_atoms):
    dist = ((b_atom.coord[0] - a_atom.coord[0])**2 + 
            (b_atom.coord[1] - a_atom.coord[1])**2 + 
            (b_atom.coord[2] - a_atom.coord[2])**2)**0.5
    distances.append((b_atom.resi, dist))

# Sort by distance
distances.sort(key=lambda x: x[1], reverse=True)

print("Top 10 residues with highest distance between BioEmu and AF2:")
for resi, dist in distances[:10]:
    print(f"Residue {resi}: {dist:.2f} Å")
