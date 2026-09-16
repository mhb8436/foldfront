import unittest

from pipeline_mcp.soluprot_server import soluprot_score


class TestSoluProtServer(unittest.TestCase):
    def test_score_is_in_range(self) -> None:
        for seq in ("", "ACDEFGHIK", "MMMMMMMMMM", "DDDDDDDDDD", "ACD EFG\nHIK"):
            score = soluprot_score(seq)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_more_charged_scores_higher_than_hydrophobic(self) -> None:
        charged = soluprot_score("DEKRH" * 20)
        hydrophobic = soluprot_score("AVLIMFWY" * 20)
        self.assertGreater(charged, hydrophobic)


if __name__ == "__main__":
    unittest.main()

