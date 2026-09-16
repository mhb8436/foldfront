import sys

with open("pipeline-mcp/src/pipeline_mcp/tools.py", "r") as f:
    content = f.read()

old_args = """    target_fasta = _as_text(args.get("target_fasta"))
    target_pdb = _as_text(args.get("target_pdb"))
    project_id = _as_text(args.get("project_id")).strip() or None"""

new_args = """    target_fasta = _as_text(args.get("target_fasta"))
    target_pdb = _as_text(args.get("target_pdb"))
    evolution_mode = _as_bool(args.get("evolution_mode"), False)
    evolution_initial_samples = _as_int(args.get("evolution_initial_samples"), 20)
    evolution_rounds = _as_int(args.get("evolution_rounds"), 3)
    evolution_samples_per_round = _as_int(args.get("evolution_samples_per_round"), 5)
    project_id = _as_text(args.get("project_id")).strip() or None"""

old_return = """    return PipelineRequest(
        target_fasta=target_fasta,
        target_pdb=target_pdb,
        project_id=project_id,"""

new_return = """    return PipelineRequest(
        target_fasta=target_fasta,
        target_pdb=target_pdb,
        evolution_mode=evolution_mode,
        evolution_initial_samples=evolution_initial_samples,
        evolution_rounds=evolution_rounds,
        evolution_samples_per_round=evolution_samples_per_round,
        project_id=project_id,"""

content = content.replace(old_args, new_args)
content = content.replace(old_return, new_return)

with open("pipeline-mcp/src/pipeline_mcp/tools.py", "w") as f:
    f.write(content)
