import sys

with open("pipeline-mcp/src/pipeline_mcp/evolution.py", "r") as f:
    content = f.read()

old_path = "af2_pdb = list((eval_path / request.af2_provider).rglob(\"*.pdb\"))"
new_path = "af2_pdb = list((eval_path / \"af2\").rglob(\"*.pdb\"))"

content = content.replace(old_path, new_path)

with open("pipeline-mcp/src/pipeline_mcp/evolution.py", "w") as f:
    f.write(content)
