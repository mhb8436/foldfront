with open("pipeline-mcp/src/pipeline_mcp/models.py", "r") as f:
    code = f.read()
if "evolution_mode" not in code:
    code = code.replace("class PipelineRequest:", "class PipelineRequest:\n    evolution_mode: bool = False")
    with open("pipeline-mcp/src/pipeline_mcp/models.py", "w") as f:
        f.write(code)
