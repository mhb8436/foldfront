import sys

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    content = f.read()

import_stmt = "from .storage import write_json\nfrom .evolution import run_evolution\n"
content = content.replace("from .storage import write_json\n", import_stmt)

run_method_start = """    def run(
        self, request: PipelineRequest, *, run_id: str | None = None
    ) -> PipelineResult:
        run_id = run_id or new_run_id("pipeline")"""

run_method_replacement = """    def run(
        self, request: PipelineRequest, *, run_id: str | None = None
    ) -> PipelineResult:
        run_id = run_id or new_run_id("pipeline")
        if getattr(request, "evolution_mode", False):
            return run_evolution(self, request, run_id)"""

content = content.replace(run_method_start, run_method_replacement)

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "w") as f:
    f.write(content)
