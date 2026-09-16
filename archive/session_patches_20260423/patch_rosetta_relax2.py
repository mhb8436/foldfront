import sys

with open("pipeline-mcp/src/pipeline_mcp/clients/rosetta_relax.py", "r") as f:
    content = f.read()

# Remove the previously added relax method
if "    def relax(" in content:
    lines = content.splitlines()
    new_lines = []
    skip = False
    for line in lines:
        if line.startswith("    def relax("):
            skip = True
        elif skip and line.startswith("    def _runtime_path"):
            skip = False
        if not skip:
            new_lines.append(line)
    content = "\n".join(new_lines) + "\n"


method_to_add = """
    def relax(self, pdb_text: str, nstruct: int = 1, extra_flags: str | None = None) -> dict[str, Any]:
        \"\"\"Legacy interface for pipeline.py compatibility.\"\"\"
        import tempfile
        from pathlib import Path
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            input_pdb = tmp_path / "input.pdb"
            input_pdb.write_text(pdb_text)
            output_dir = tmp_path / "output"
            
            result = self.run(input_pdb, output_dir, nstruct, extra_flags)
            
            best_pdb_path = result.get("best_pdb")
            best_pdb_text = best_pdb_path.read_text() if best_pdb_path and best_pdb_path.exists() else ""
            
            res_count = sum(1 for line in best_pdb_text.splitlines() if line.startswith("ATOM") and line[12:16].strip() == "CA")
            score_per_residue = result.get("score_per_residue", 0.0)
            true_total_score = score_per_residue * max(res_count, 1)
            
            return {
                "best_pdb_text": best_pdb_text,
                "total_score": true_total_score,
                "delta_total_score": 0.0,
                "input_total_score": 0.0,
                "description": best_pdb_path.stem if best_pdb_path else "",
                "mode": self._mode(),
            }

    def _runtime_path"""

content = content.replace("    def _runtime_path", method_to_add)

with open("pipeline-mcp/src/pipeline_mcp/clients/rosetta_relax.py", "w") as f:
    f.write(content)
