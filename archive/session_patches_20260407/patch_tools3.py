with open("pipeline-mcp/src/pipeline_mcp/tools.py", "r") as f:
    code = f.read()

# Add a hook for evolution mode in call_tool
# Instead of res = _run_with_auto_retry(self.runner, req, run_id=normalized_run_id, retry=retry)
# If req.evolution_mode is True, run the BO loop!
