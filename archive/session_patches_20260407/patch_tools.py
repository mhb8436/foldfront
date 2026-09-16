import re
with open("pipeline-mcp/src/pipeline_mcp/tools.py", "r") as f:
    code = f.read()

# Add evolution_mode boolean to the tools definition
if "evolution_mode" not in code:
    old = '"dry_run": {"type": "boolean"},'
    new = '"dry_run": {"type": "boolean"},\n                "evolution_mode": {"type": "boolean", "description": "Run in 3-round multi-stage evolution mode"},'
    code = code.replace(old, new)
    with open("pipeline-mcp/src/pipeline_mcp/tools.py", "w") as f:
        f.write(code)
