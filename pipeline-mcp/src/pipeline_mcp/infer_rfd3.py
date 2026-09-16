import json

def get_inferred_enzyme_fields(pdb_text: str):
    from pipeline_mcp.bio.pdb import residues_by_chain
    by_chain = residues_by_chain(pdb_text)
    if not by_chain:
        return None
    # Sort chains alphabetically
    first_chain = sorted(by_chain.keys())[0]
    residues = by_chain[first_chain]
    if len(residues) <= 1:
        return None
    
    first_res = residues[0]
    last_res = residues[-1]
    
    unindex = f"{first_chain}{first_res.resseq}"
    contig = f"{first_chain}{residues[1].resseq}-{last_res.resseq}"
    select_fixed_atoms = {unindex: "ALL"}
    
    return {
        "unindex": unindex,
        "contig": contig,
        "select_fixed_atoms": select_fixed_atoms
    }
