with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    code = f.read()

# Fix the import issue causing the test to fail:
old = """                from .bio.pdb import _has_nonpositive_resseq, _prepare_pdb_text_for_design_context
                # Renumber residues from 1 temporarily for inference if needed, because RFD3 fails on negative contigs"""

new = """                # Renumber residues from 1 temporarily for inference if needed, because RFD3 fails on negative contigs"""

# And remove it if I already replaced it
code = code.replace(old, new)
code = code.replace("from .bio.pdb import _has_nonpositive_resseq, _prepare_pdb_text_for_design_context", "")

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "w") as f:
    f.write(code)
