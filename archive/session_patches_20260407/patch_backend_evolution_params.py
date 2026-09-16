import re

# Update models.py
with open("pipeline-mcp/src/pipeline_mcp/models.py", "r") as f:
    code = f.read()

old_params = "    evolution_mode: bool = False"
new_params = """    evolution_mode: bool = False
    evolution_initial_samples: int = 20
    evolution_rounds: int = 3
    evolution_samples_per_round: int = 5"""

if "evolution_initial_samples" not in code:
    code = code.replace(old_params, new_params)
    with open("pipeline-mcp/src/pipeline_mcp/models.py", "w") as f:
        f.write(code)

# Update tools.py schema
with open("pipeline-mcp/src/pipeline_mcp/tools.py", "r") as f:
    tools_code = f.read()

if "evolution_initial_samples" not in tools_code:
    old_schema = '"evolution_mode": {"type": "boolean", "description": "Run in 3-round multi-stage evolution mode"},'
    new_schema = '"evolution_mode": {"type": "boolean", "description": "Run in 3-round multi-stage evolution mode"},\n                "evolution_initial_samples": {"type": "integer", "description": "Initial random samples to evaluate (default 20)"},\n                "evolution_rounds": {"type": "integer", "description": "Number of BO rounds (default 3)"},\n                "evolution_samples_per_round": {"type": "integer", "description": "Samples to evaluate per BO round (default 5)"},'
    tools_code = tools_code.replace(old_schema, new_schema)
    
    old_parse = 'request.evolution_mode = _as_bool(args.get("evolution_mode"), False)'
    new_parse = 'request.evolution_mode = _as_bool(args.get("evolution_mode"), False)\n    request.evolution_initial_samples = _as_int(args.get("evolution_initial_samples"), 20)\n    request.evolution_rounds = _as_int(args.get("evolution_rounds"), 3)\n    request.evolution_samples_per_round = _as_int(args.get("evolution_samples_per_round"), 5)'
    tools_code = tools_code.replace(old_parse, new_parse)
    
    with open("pipeline-mcp/src/pipeline_mcp/tools.py", "w") as f:
        f.write(tools_code)
