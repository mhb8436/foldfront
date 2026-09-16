import os
import unittest

from pipeline_mcp.pipeline import _prepare_af2_sequence


class TestAF2InputValidation(unittest.TestCase):
    def test_invalid_characters_fail_fast(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _prepare_af2_sequence("ACD*EF", model_preset="monomer", chain_ids=None)
        self.assertIn("AF2 input validation failed", str(ctx.exception))

    def test_monomer_multichain_fails_by_default(self) -> None:
        with self.assertRaises(ValueError):
            _prepare_af2_sequence("ACD/EF", model_preset="monomer", chain_ids=["A", "B"])

    def test_monomer_multichain_can_use_first_chain_with_env(self) -> None:
        os.environ["PIPELINE_AF2_MONOMER_FIRST_CHAIN"] = "1"
        try:
            out = _prepare_af2_sequence("ACD/EF", model_preset="monomer", chain_ids=["A", "B"])
        finally:
            del os.environ["PIPELINE_AF2_MONOMER_FIRST_CHAIN"]
        self.assertEqual(out, "ACD")

    def test_multimer_converts_chain_delimiter_to_colon(self) -> None:
        # The worker only understands ':' for a complex; see
        # tests/test_af2_multimer_chain_delimiter.py for the full contract.
        out = _prepare_af2_sequence("ACD/EF", model_preset="multimer", chain_ids=["A", "B"])
        self.assertEqual(out, "ACD:EF")


if __name__ == "__main__":
    unittest.main()

