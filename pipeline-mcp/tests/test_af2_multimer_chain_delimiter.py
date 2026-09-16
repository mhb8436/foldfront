"""Multimer chain delimiter handling for the AF2/ColabFold input path.

Regression cover for the 'CHAIN' fusion bug: `_prepare_af2_sequence` used to
embed a pseudo-FASTA header ('\n>chain_2\n') inside the single `sequence`
string sent to the worker. The worker keeps only letters, so 'chain_2'
survived as the five residues C,H,A,I,N and the two chains were folded as one
fused polypeptide -- while the run still reported success.
"""

import unittest

from pipeline_mcp.pipeline import _prepare_af2_sequence


class TestMultimerChainDelimiter(unittest.TestCase):
    def test_colabfold_multimer_uses_colon_delimiter(self) -> None:
        out = _prepare_af2_sequence(
            "ACD/EF", model_preset="multimer", chain_ids=["A", "B"], provider="colabfold"
        )
        self.assertEqual(out, "ACD:EF")

    def test_colabfold_multimer_never_emits_a_pseudo_fasta_header(self) -> None:
        out = _prepare_af2_sequence(
            "ACD/EF", model_preset="multimer", chain_ids=None, provider="colabfold"
        )
        self.assertNotIn(">", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("CHAIN", out.upper())

    def test_colabfold_multimer_three_chains(self) -> None:
        out = _prepare_af2_sequence(
            "ACD/EF/GH", model_preset="multimer", chain_ids=None, provider="colabfold"
        )
        self.assertEqual(out, "ACD:EF:GH")

    def test_stock_af2_provider_rejects_multimer_instead_of_fusing(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _prepare_af2_sequence(
                "ACD/EF", model_preset="multimer", chain_ids=None, provider="af2"
            )
        msg = str(ctx.exception)
        self.assertIn("af2_provider", msg)
        self.assertIn("colabfold", msg)

    def test_monomer_is_unchanged_by_the_provider_argument(self) -> None:
        out = _prepare_af2_sequence(
            "ACD", model_preset="monomer", chain_ids=["A"], provider="af2"
        )
        self.assertEqual(out, "ACD")

    def test_provider_defaults_to_colabfold(self) -> None:
        out = _prepare_af2_sequence("ACD/EF", model_preset="multimer", chain_ids=None)
        self.assertEqual(out, "ACD:EF")


if __name__ == "__main__":
    unittest.main()
