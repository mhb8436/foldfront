with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    code = f.read()

old = "pdb_text = _prepare_pdb_text_for_design_context(pdb_text, strip_nonpositive_resseq=False, renumber_resseq_from_1=True)"
new = "pdb_text = _prepare_pdb_text_for_design_context(pdb_text, chains=None, strip_nonpositive_resseq=False, renumber_resseq_from_1=True)"

code = code.replace(old, new)

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "w") as f:
    f.write(code)
