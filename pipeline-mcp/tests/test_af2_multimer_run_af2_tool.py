"""End-to-end cover for pipeline.run_af2 multimer handling.

Guards the two halves of the 'CHAIN' fusion bug: the sequence that actually
reaches the worker, and the refusal to report a fused single-chain prediction
as a successful multimer.
"""

import ast
import json
import unittest
import uuid
from pathlib import Path

from pipeline_mcp.pipeline import PipelineRunner
from pipeline_mcp.tools import ToolDispatcher


def _tmp_root() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"af2mm_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _pdb(chains: dict[str, int]) -> str:
    lines = []
    serial = 0
    for chain_id, n_res in chains.items():
        for resseq in range(1, n_res + 1):
            serial += 1
            lines.append(
                f"ATOM  {serial:5d}  CA  ALA {chain_id}{resseq:4d}    "
                f"{float(resseq):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 90.00           C"
            )
    lines.append("END")
    return "\n".join(lines) + "\n"


class _RecordingColabFold:
    endpoint_id = "fake-colabfold"

    def __init__(self, pdb_text: str) -> None:
        self.calls: list[dict] = []
        self._pdb_text = pdb_text

    def predict(
        self,
        sequences,
        *,
        model_preset="monomer",
        db_preset="full_dbs",
        max_template_date="2020-05-14",
        extra_flags=None,
        on_job_id=None,
        resume_job_ids=None,
    ):
        self.calls.append(
            {
                "sequences": [(s.id, s.sequence) for s in sequences],
                "model_preset": model_preset,
            }
        )
        return {
            s.id: {
                "best_plddt": 92.5,
                "best_model": "model_1_multimer_v3",
                "ranked_0_pdb": self._pdb_text,
            }
            for s in sequences
        }


class TestRunAf2Multimer(unittest.TestCase):
    def _dispatch(self, arguments: dict, *, pdb_text: str):
        root = _tmp_root()
        client = _RecordingColabFold(pdb_text)
        runner = PipelineRunner(output_root=root, colabfold=client)
        dispatcher = ToolDispatcher(runner)
        return dispatcher, client, Path(root)

    def test_multimer_reaches_the_worker_as_colon_joined_chains(self) -> None:
        dispatcher, client, _ = self._dispatch({}, pdb_text=_pdb({"A": 3, "B": 2}))
        dispatcher.call_tool(
            "pipeline.run_af2",
            {
                "fasta": ">homodimer\nACD/EF\n",
                "af2_model_preset": "multimer",
                "af2_provider": "colabfold",
            },
        )
        self.assertEqual(len(client.calls), 1)
        seq_id, sequence = client.calls[0]["sequences"][0]
        self.assertEqual(sequence, "ACD:EF")
        self.assertEqual(client.calls[0]["model_preset"], "multimer")

    def test_request_json_records_chain_ids_and_sequence_id(self) -> None:
        dispatcher, _, root = self._dispatch({}, pdb_text=_pdb({"A": 3, "B": 2}))
        out = dispatcher.call_tool(
            "pipeline.run_af2",
            {
                "fasta": ">homodimer\nACD/EF\n",
                "sequence_id": "homodimer",
                "af2_model_preset": "multimer",
                "af2_chain_ids": ["A", "B"],
            },
        )
        request = json.loads(
            (Path(out["output_dir"]) / "request.json").read_text()
        )
        self.assertEqual(request["af2_chain_ids"], ["A", "B"])
        self.assertEqual(request["sequence_id"], "homodimer")

    def test_chain_id_count_mismatch_fails_fast(self) -> None:
        dispatcher, client, _ = self._dispatch({}, pdb_text=_pdb({"A": 3, "B": 2}))
        with self.assertRaises(ValueError) as ctx:
            dispatcher.call_tool(
                "pipeline.run_af2",
                {
                    "fasta": ">homodimer\nACD/EF\n",
                    "af2_model_preset": "multimer",
                    "af2_chain_ids": ["A", "B", "C"],
                },
            )
        self.assertIn("af2_chain_ids", str(ctx.exception))
        self.assertEqual(client.calls, [])

    def test_single_chain_prediction_for_a_multimer_is_a_failure(self) -> None:
        dispatcher, _, _ = self._dispatch({}, pdb_text=_pdb({"A": 5}))
        out = dispatcher.call_tool(
            "pipeline.run_af2",
            {
                "fasta": ">homodimer\nACD/EF\n",
                "af2_model_preset": "multimer",
            },
        )
        summary = out["summary"]
        self.assertEqual(summary["completed_count"], 0)
        self.assertEqual(summary["failed_count"], 1)
        failure = " ".join(str(v) for v in summary["failures"].values())
        self.assertIn("chain", failure.lower())

    def test_two_chain_prediction_for_a_multimer_succeeds(self) -> None:
        dispatcher, _, _ = self._dispatch({}, pdb_text=_pdb({"A": 3, "B": 2}))
        out = dispatcher.call_tool(
            "pipeline.run_af2",
            {
                "fasta": ">homodimer\nACD/EF\n",
                "af2_model_preset": "multimer",
            },
        )
        self.assertEqual(out["summary"]["completed_count"], 1)
        self.assertEqual(out["summary"]["failed_count"], 0)

    def test_stock_af2_provider_refuses_a_multimer_end_to_end(self) -> None:
        root = _tmp_root()
        client = _RecordingColabFold(_pdb({"A": 3, "B": 2}))
        runner = PipelineRunner(output_root=root, af2=client)
        dispatcher = ToolDispatcher(runner)
        with self.assertRaises(ValueError) as ctx:
            dispatcher.call_tool(
                "pipeline.run_af2",
                {
                    "fasta": ">homodimer\nACD/EF\n",
                    "af2_model_preset": "multimer",
                    "af2_provider": "af2",
                },
            )
        self.assertIn("colabfold", str(ctx.exception))
        self.assertEqual(client.calls, [])

    def test_monomer_single_chain_prediction_is_untouched(self) -> None:
        dispatcher, client, _ = self._dispatch({}, pdb_text=_pdb({"A": 3}))
        out = dispatcher.call_tool(
            "pipeline.run_af2",
            {"fasta": ">m\nACD\n", "af2_model_preset": "monomer"},
        )
        self.assertEqual(client.calls[0]["sequences"][0][1], "ACD")
        self.assertEqual(out["summary"]["completed_count"], 1)


class TestMultimerDryRunPreview(unittest.TestCase):
    def test_dry_run_previews_one_chain_per_requested_chain(self) -> None:
        runner = PipelineRunner(output_root=_tmp_root())
        out = ToolDispatcher(runner).call_tool(
            "pipeline.run_af2",
            {
                "fasta": ">homodimer\nACD/EF\n",
                "af2_model_preset": "multimer",
                "dry_run": True,
            },
        )
        results = json.loads(
            (Path(out["output_dir"]) / "af2" / "results.json").read_text()
        )
        pdb_text = next(iter(results.values()))["ranked_0_pdb"]
        chains = {line[21] for line in pdb_text.splitlines() if line.startswith("ATOM")}
        self.assertEqual(chains, {"A", "B"})


class TestPrepareAf2SequenceCallSites(unittest.TestCase):
    """A caller that omits provider= silently defaults to ColabFold, which would
    fuse a multimer again on the stock AlphaFold2 worker."""

    def test_every_call_site_passes_an_explicit_provider(self) -> None:
        src_dir = Path(__file__).resolve().parents[1] / "src" / "pipeline_mcp"
        offenders: list[str] = []
        for path in sorted(src_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name != "_prepare_af2_sequence":
                    continue
                if not any(kw.arg == "provider" for kw in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
