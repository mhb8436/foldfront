import sys

with open("pipeline-mcp/src/pipeline_mcp/evolution.py", "r") as f:
    content = f.read()

old_return = """    return PipelineResult(
        run_id=run_id,
        output_dir=str(paths.root),
        summary=summary,
        errors=[],
    )"""

new_return = """    return PipelineResult(
        run_id=run_id,
        output_dir=str(paths.root),
        msa_a3m_path=None,
        msa_filtered_a3m_path=None,
        msa_tsv_path=None,
        conservation_path=None,
        ligand_mask_path=None,
        surface_mask_path=None,
        tiers=[],
        errors=[],
    )"""

content = content.replace(old_return, new_return)

with open("pipeline-mcp/src/pipeline_mcp/evolution.py", "w") as f:
    f.write(content)
