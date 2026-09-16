with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    code = f.read()

# I also need to change `_rfd3_simple_inputs` where it does:
# if _has_nonpositive_resseq(pdb_text):
#     pdb_text = _prepare_pdb_text_for_design_context(pdb_text, strip_nonpositive_resseq=True, renumber_resseq_from_1=False)
# to renumber_resseq_from_1=True

old = """                # Strip negative residues temporarily for inference if needed, because RFD3 fails on negative contigs
                if _has_nonpositive_resseq(pdb_text):
                    pdb_text = _prepare_pdb_text_for_design_context(pdb_text, strip_nonpositive_resseq=True, renumber_resseq_from_1=False)"""

new = """                # Renumber residues from 1 temporarily for inference if needed, because RFD3 fails on negative contigs
                if _has_nonpositive_resseq(pdb_text):
                    pdb_text = _prepare_pdb_text_for_design_context(pdb_text, strip_nonpositive_resseq=False, renumber_resseq_from_1=True)"""

code = code.replace(old, new)

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "w") as f:
    f.write(code)
