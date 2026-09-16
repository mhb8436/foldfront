import sys

with open("pipeline-mcp/src/pipeline_mcp/clients/rosetta_relax.py", "r") as f:
    content = f.read()

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
            
            return {
                "best_pdb_text": best_pdb_text,
                "total_score": result.get("best_score", 0.0),
                "delta_total_score": 0.0,
                "input_total_score": 0.0,
                "description": best_pdb_path.stem if best_pdb_path else "",
                "mode": self._mode(),
            }

    def _runtime_path"""

content = content.replace("    def _runtime_path", method_to_add)

with open("pipeline-mcp/src/pipeline_mcp/clients/rosetta_relax.py", "w") as f:
    f.write(content)
