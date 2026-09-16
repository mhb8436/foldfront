import unittest

from pipeline_mcp.bio.alignment import global_alignment_mapping


class TestGlobalAlignmentMapping(unittest.TestCase):
    def test_identical_sequence_maps_identity(self) -> None:
        res = global_alignment_mapping("ACDE", "ACDE")
        self.assertEqual(res.mapping_query_to_target, [1, 2, 3, 4])
        self.assertEqual(res.matches, 4)
        self.assertEqual(res.aligned_pairs, 4)
        self.assertAlmostEqual(res.pairwise_identity, 1.0)
        self.assertAlmostEqual(res.query_identity, 1.0)

    def test_nterm_truncation_maps_to_none_prefix(self) -> None:
        res = global_alignment_mapping("ACDEFG", "DEFG")
        self.assertEqual(res.mapping_query_to_target, [None, None, 1, 2, 3, 4])
        self.assertEqual(res.matches, 4)
        self.assertAlmostEqual(res.pairwise_identity, 1.0)
        self.assertAlmostEqual(res.query_identity, 4 / 6)

    def test_internal_deletion_introduces_gap(self) -> None:
        res = global_alignment_mapping("ABCDE", "ABDE")
        self.assertEqual(res.mapping_query_to_target, [1, 2, None, 3, 4])
        self.assertEqual(res.matches, 4)
        self.assertAlmostEqual(res.pairwise_identity, 1.0)
        self.assertAlmostEqual(res.query_identity, 4 / 5)

    def test_internal_insertion_skips_target_positions(self) -> None:
        res = global_alignment_mapping("ABDE", "ABCDE")
        self.assertEqual(res.mapping_query_to_target, [1, 2, 4, 5])
        self.assertEqual(res.matches, 4)
        self.assertAlmostEqual(res.pairwise_identity, 1.0)
        self.assertAlmostEqual(res.target_identity, 4 / 5)


if __name__ == "__main__":
    unittest.main()

