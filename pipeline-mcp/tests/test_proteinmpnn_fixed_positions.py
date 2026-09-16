import unittest

from pipeline_mcp.models import SequenceRecord
from pipeline_mcp.pipeline import _validate_proteinmpnn_fixed_positions


class TestProteinMPNNFixedPositionsCheck(unittest.TestCase):
    def test_handles_chain_separator_slash(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        fixed = {"A": [1], "B": [1]}
        native = SequenceRecord(id="native", header="native", sequence="AC/DE", meta={})
        samples = [
            SequenceRecord(id="1", header="1", sequence="AX/DF", meta={}),  # fixed A1=A, B1=D preserved
        ]

        check = _validate_proteinmpnn_fixed_positions(
            pdb_text=pdb,
            design_chains=["A", "B"],
            fixed_positions_by_chain=fixed,
            native=native,
            samples=samples,
        )

        self.assertEqual(check.get("errors"), [])
        self.assertTrue(bool(check.get("ok")))


if __name__ == "__main__":
    unittest.main()

