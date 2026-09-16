import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
import time
from pathlib import Path
from unittest import mock

import numpy as np


def _load_module(module_name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCathDatasetMode(unittest.TestCase):
    def test_build_cath_request_uses_input_only_dense_labels(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        request = module.build_cath_request("END\n")

        self.assertFalse(bool(request.rfd3_use))
        self.assertFalse(bool(request.bioemu_use))
        self.assertEqual(int(request.num_seq_per_tier), 40)
        self.assertEqual(float(request.soluprot_cutoff), 0.0)
        self.assertEqual(int(request.af2_max_candidates_per_tier), 0)
        self.assertEqual(int(request.af2_top_k), 0)
        self.assertFalse(bool(request.relax_enabled))
        self.assertFalse(bool(request.novelty_enabled))
        self.assertFalse(bool(request.wt_compare))
        self.assertEqual(str(request.stop_after), "af2")

    def test_build_cath_request_uses_cath_chain_and_first_model_sequence(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script_chain_model",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        pdb = (
            "HEADER    TEST\n"
            "MODEL        1\n"
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ENDMDL\n"
            "MODEL        2\n"
            "ATOM      3  CA  VAL A   1       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  LEU A   2       3.000   0.000   0.000  1.00 20.00           C\n"
            "ENDMDL\n"
            "END\n"
        )

        request = module.build_cath_request(pdb, target_id="demoA00")

        self.assertEqual(request.design_chains, ["A"])
        self.assertIn(">demoA00_A", request.target_fasta)
        self.assertIn("AG", request.target_fasta)
        self.assertNotIn("VL", request.target_fasta)
        self.assertNotIn("MODEL        2", request.target_pdb)

    def test_build_cath_request_prefers_chain_from_cath_domain_id(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script_chain_id",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  THR B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  TYR B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        request = module.build_cath_request(pdb, target_id="demoB00")

        self.assertEqual(request.design_chains, ["B"])
        self.assertIn(">demoB00_B", request.target_fasta)
        self.assertIn("TY", request.target_fasta)
        self.assertNotIn("AGS", request.target_fasta)

    def test_train_cath_surrogate_exports_relax_model(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "train_cath_surrogate_script",
            repo_root / "scripts" / "train_cath_surrogate.py",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs_root = root / "outputs"
            run_root = outputs_root / "cath_train_demo"
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)

            samples = []
            soluprot_scores: dict[str, float] = {}
            plddt_scores: dict[str, float] = {}
            relax_scores: dict[str, float] = {}
            for idx in range(20):
                seq_id = f"seq_{idx:02d}"
                sequence = "ACDEFGHIKLMNPQRSTVWY"[idx % 20 :] + "ACDEFGHIKLMNPQRSTVWY"[: idx % 20]
                samples.append({"id": seq_id, "sequence": sequence})
                soluprot_scores[seq_id] = round(0.2 + (idx * 0.01), 4)
                plddt_scores[seq_id] = float(70 + idx)
                relax_scores[seq_id] = float(-3.5 + (idx * 0.05))

            (run_root / "summary.json").write_text(
                json.dumps({"tiers": [{"tier": 0.3, "proteinmpnn_samples": samples}]}),
                encoding="utf-8",
            )
            (tier_dir / "soluprot.json").write_text(
                json.dumps({"scores": soluprot_scores}),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps({"scores": plddt_scores}),
                encoding="utf-8",
            )
            (tier_dir / "relax_scores.json").write_text(
                json.dumps({"score_per_residue": relax_scores}),
                encoding="utf-8",
            )

            module.PROJECT_ROOT = root
            module.OUTPUTS_ROOT = outputs_root
            module.META_ROOT = root / "meta_surrogate_prototype"
            module.MODEL_ROOT = root / "pipeline-mcp" / "models"
            module._generate_embeddings = lambda sequences, model_name: np.arange(
                len(sequences) * 4, dtype=float
            ).reshape(len(sequences), 4)
            module._train_regressor = (
                lambda X, y, hidden_layers: {
                    "rows": int(len(y)),
                    "hidden_layers": tuple(hidden_layers),
                }
            )

            with mock.patch.object(sys, "argv", ["train_cath_surrogate.py", "--subsets", "train"]):
                exit_code = module.main()

            self.assertEqual(exit_code, 0)
            relax_model = module.MODEL_ROOT / "global_relax_v1.pkl"
            self.assertTrue(relax_model.exists())

            summary_path = module.META_ROOT / "training_summary_train.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["artifacts"]["relax_model"], str(relax_model))

    def test_cath_lock_blocks_live_duplicate(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script_lock",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        with tempfile.TemporaryDirectory() as tmp:
            module.project_root = Path(tmp)
            first = module.acquire_cath_lock(
                "batch_test",
                {"kind": "cath_batch", "subset": "test"},
            )
            self.assertIsNotNone(first)
            try:
                second = module.acquire_cath_lock(
                    "batch_test",
                    {"kind": "cath_batch", "subset": "test"},
                )
                self.assertIsNone(second)
            finally:
                module.release_cath_lock(first)

    def test_launch_cath_batch_reuses_active_subset_job(self) -> None:
        from pipeline_mcp import cath_ops

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "outputs"
            output_root.mkdir()
            job_id = "cath_batch_existing"
            job_root = cath_ops.managed_jobs_root(str(output_root)) / job_id
            job_root.mkdir(parents=True)
            (job_root / "job.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "kind": "cath_batch",
                        "state": "running",
                        "helper_pid": os.getpid(),
                        "created_at": "2026-05-06T00:00:00Z",
                        "metadata": {"subset": "test", "max_workers": 2},
                    }
                ),
                encoding="utf-8",
            )

            result = cath_ops.launch_cath_batch_job(
                str(output_root),
                subset="test",
                max_workers=2,
            )

            self.assertEqual(result["job_id"], job_id)
            self.assertTrue(result["already_running"])

    def test_prepare_cath_run_skips_recent_running_status(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script_recent_running",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.project_root = root
            run_id = "cath_test_recent"
            run_root = root / "outputs" / run_id
            run_root.mkdir(parents=True)
            (run_root / "status.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "stage": "af2_30",
                        "state": "running",
                        "updated_at": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.gmtime()
                        ),
                    }
                ),
                encoding="utf-8",
            )

            calls: list[str] = []
            decision = module.prepare_cath_run_for_start(
                object(),
                run_id,
                stale_after_seconds=3600,
                cancel_func=lambda _runner, rid: calls.append(rid) or True,
            )

            self.assertEqual(decision, "skip_recent_running")
            self.assertEqual(calls, [])
            status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "running")

    def test_prepare_cath_run_cancels_stale_running_status(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        module = _load_module(
            "run_cath_batch_script_stale_running",
            repo_root / "scripts" / "02_run_cath_batch.py",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.project_root = root
            run_id = "cath_test_stale"
            run_root = root / "outputs" / run_id
            run_root.mkdir(parents=True)
            (run_root / "status.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "stage": "mmseqs_msa",
                        "state": "running",
                        "updated_at": "2000-01-01 00:00:00",
                    }
                ),
                encoding="utf-8",
            )

            calls: list[str] = []
            decision = module.prepare_cath_run_for_start(
                object(),
                run_id,
                stale_after_seconds=60,
                cancel_func=lambda _runner, rid: calls.append(rid) or True,
            )

            self.assertEqual(decision, "cancelled_stale")
            self.assertEqual(calls, [run_id])


if __name__ == "__main__":
    unittest.main()
