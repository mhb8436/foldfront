with open("pipeline-mcp/src/pipeline_mcp/tools.py", "r") as f:
    code = f.read()

# Make pipeline_request_from_args parse evolution_mode
old = 'request.pdb_renumber_resseq_from_1 = _as_bool(args.get("pdb_renumber_resseq_from_1"), False)'
new = 'request.pdb_renumber_resseq_from_1 = _as_bool(args.get("pdb_renumber_resseq_from_1"), False)\n    request.evolution_mode = _as_bool(args.get("evolution_mode"), False)'

if "request.evolution_mode =" not in code:
    code = code.replace(old, new)
    with open("pipeline-mcp/src/pipeline_mcp/tools.py", "w") as f:
        f.write(code)
