import unittest

from pipeline_mcp.bio.pdb import surface_positions_by_chain
from pipeline_mcp.bio.sequence import filter_records_by_pi
from pipeline_mcp.bio.sequence import isoelectric_point
from pipeline_mcp.models import SequenceRecord


class TestSurfaceAndPI(unittest.TestCase):
    def test_isoelectric_point_bounds(self) -> None:
        self.assertLess(isoelectric_point("DDDDDD"), 4.5)
        self.assertGreater(isoelectric_point("KKKKKK"), 9.0)

    def test_pi_filter(self) -> None:
        records = [
            SequenceRecord(id="acid", sequence="DDDDDD"),
            SequenceRecord(id="base", sequence="KKKKKK"),
        ]
        passed, scores = filter_records_by_pi(records, pi_max=6.0)
        passed_ids = {r.id for r in passed}
        self.assertIn("acid", passed_ids)
        self.assertNotIn("base", passed_ids)
        self.assertIn("acid", scores)
        self.assertIn("base", scores)

    def test_surface_positions_by_chain(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2      20.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        surface, sasa = surface_positions_by_chain(pdb, points_per_atom=20, min_rel=0.05, min_abs=5.0)
        self.assertIn("A", surface)
        self.assertEqual(surface["A"], [1, 2])
        self.assertIn("A", sasa)
        self.assertIn(1, sasa["A"])
        self.assertIn(2, sasa["A"])


if __name__ == "__main__":
    unittest.main()
