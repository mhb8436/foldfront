import sys
from Bio.PDB import PDBParser

parser = PDBParser()
bioemu_pdb = './outputs/admin_20260325_061633_2249cbdc/bioemu/designs/sample_0001.pdb'
af2_pdb = './outputs/admin_20260325_061633_2249cbdc/tiers/30/af2/sample_0001_1/ranked_0.pdb'

bioemu_struct = parser.get_structure("bioemu", bioemu_pdb)
af2_struct = parser.get_structure("af2", af2_pdb)

from Bio.SeqUtils import seq1

def get_seq(struct):
    seq = ""
    for model in struct:
        for chain in model:
            for res in chain:
                if res.id[0] == ' ':
                    seq += seq1(res.resname)
    return seq

print("BioEmu:", get_seq(bioemu_struct))
print("AF2:   ", get_seq(af2_struct))
