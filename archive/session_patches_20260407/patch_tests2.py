with open("pipeline-mcp/tests/test_pipeline_dry_run.py", "r") as f:
    code = f.read()

# Revert test_pipeline_rfd3_input_only_spec_leaves_fixed_atoms_unset_with_request_default_partial_t
t1_old = """            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 10.0)
            self.assertNotIn("partial_T", spec)
            self.assertIn("select_fixed_atoms", spec)
            self.assertEqual(spec["select_fixed_atoms"], {"A1": "ALL"})
            self.assertEqual(spec["unindex"], "A1")"""
t1_new = """            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 10.0)
            self.assertNotIn("partial_T", spec)
            self.assertNotIn("select_fixed_atoms", spec)"""
# Wait, let's just do a simple replace
code = code.replace(
"""            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 10.0)
            self.assertNotIn("partial_T", spec)
            self.assertIn("select_fixed_atoms", spec)
            self.assertEqual(spec["select_fixed_atoms"], {"A1": "ALL"})
            self.assertEqual(spec["unindex"], "A1")

    def test_pipeline_rfd3_partial_t_respects_override(self) -> None:""", 
"""            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 10.0)
            self.assertNotIn("partial_T", spec)
            self.assertNotIn("select_fixed_atoms", spec)

    def test_pipeline_rfd3_partial_t_respects_override(self) -> None:""")

# Revert test_pipeline_rfd3_local_diversify_passthroughs_unindex_and_fixed_atoms
t2_old = """            self.assertEqual(spec.get("select_fixed_atoms"), {"A2": "ALL"})
            self.assertIn("contig", spec)
            self.assertEqual(spec["contig"], "A2-3")
            self.assertNotIn("partial_T", spec)"""
t2_new = """            self.assertEqual(spec.get("select_fixed_atoms"), {"A2": "ALL"})
            self.assertNotIn("contig", spec)
            self.assertNotIn("partial_T", spec)"""
code = code.replace(t2_old, t2_new)

with open("pipeline-mcp/tests/test_pipeline_dry_run.py", "w") as f:
    f.write(code)
