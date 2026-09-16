from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "benchmark" / "backbone_ensemble_ablation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backbone_ensemble_ablation", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackboneEnsembleAblationTests(unittest.TestCase):
    def test_arm_request_configs_are_budget_matched(self):
        module = _load_module()
        pdb_text = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"

        single = module.build_request(pdb_text, "single", seed=7)
        bioemu = module.build_request(pdb_text, "bioemu", seed=7)
        rfd3_single = module.build_request(pdb_text, "rfd3_single", seed=7)
        rfd3_bioemu = module.build_request(pdb_text, "rfd3_bioemu", seed=7)
        ensemble = module.build_request(pdb_text, "rfd3_ensemble3", seed=7)

        self.assertFalse(bool(single.rfd3_use))
        self.assertFalse(bool(single.rfd3_use_ensemble))
        self.assertEqual(single.num_seq_per_tier, 40)

        self.assertFalse(bool(bioemu.rfd3_use))
        self.assertTrue(bool(bioemu.bioemu_use))
        self.assertEqual(bioemu.bioemu_num_samples, 10)
        self.assertEqual(bioemu.bioemu_max_return_structures, 3)
        self.assertEqual(bioemu.num_seq_per_tier, 10)

        self.assertTrue(bool(rfd3_single.rfd3_use))
        self.assertFalse(bool(rfd3_single.rfd3_use_ensemble))
        self.assertEqual(rfd3_single.rfd3_max_return_designs, 1)
        self.assertEqual(rfd3_single.num_seq_per_tier, 40)

        self.assertTrue(bool(rfd3_bioemu.rfd3_use))
        self.assertTrue(bool(rfd3_bioemu.bioemu_use))
        self.assertEqual(rfd3_bioemu.rfd3_max_return_designs, 1)
        self.assertEqual(rfd3_bioemu.bioemu_max_return_structures, 3)
        self.assertEqual(rfd3_bioemu.num_seq_per_tier, 10)

        self.assertTrue(bool(ensemble.rfd3_use))
        self.assertTrue(bool(ensemble.rfd3_use_ensemble))
        self.assertEqual(ensemble.rfd3_max_return_designs, 3)
        self.assertEqual(ensemble.num_seq_per_tier, 13)

        for request in (single, bioemu, rfd3_single, rfd3_bioemu, ensemble):
            self.assertEqual(request.af2_max_candidates_per_tier, 10)
            self.assertEqual(request.af2_top_k, 0)
            self.assertFalse(bool(request.relax_enabled))
            self.assertFalse(bool(request.novelty_enabled))
            self.assertEqual(request.stop_after, "af2")

    def test_target_to_pdb_path_uses_manifest_split_paths(self):
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_dir = root / "data" / "benchmark" / "results"
            manifest_dir.mkdir(parents=True)
            pdb_dir = root / "cath_val"
            pdb_dir.mkdir()
            pdb_path = pdb_dir / "1abcA00.pdb"
            pdb_path.write_text("ATOM\n", encoding="utf-8")
            (manifest_dir / "rapid_target_manifest.csv").write_text(
                "split,target,pdb_path,selected_for_structural_context\n"
                f"val,1abcA00,{root / 'missing' / '1abcA00.pdb'},True\n",
                encoding="utf-8",
            )

            original_root = module.PROJECT_ROOT
            try:
                module.PROJECT_ROOT = root
                self.assertEqual(module._target_to_pdb_path("1abcA00"), pdb_path)
                self.assertEqual(module._target_to_pdb_path("cath_val_1abcA00"), pdb_path)
            finally:
                module.PROJECT_ROOT = original_root

    def test_collect_run_rows_and_summarize_arm(self):
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "abl_be_1abcA00_single_s7"
            tier_dir = run_dir / "tiers" / "30"
            tier_dir.mkdir(parents=True)
            (tier_dir / "designs_filtered.fasta").write_text(
                ">target:1|backbone=target|source=target\nAAAA\n"
                ">target:2|backbone=target|source=target\nAAAT\n"
                ">target:3|backbone=target|source=target\nAATT\n",
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps({"scores": {"target:1": 90.0, "target:2": 80.0}}),
                encoding="utf-8",
            )
            (tier_dir / "soluprot.json").write_text(
                json.dumps({"scores": {"target:1": 0.7, "target:2": 0.4, "target:3": 0.9}}),
                encoding="utf-8",
            )

            rows = module.collect_run_rows(
                run_dir,
                target="1abcA00",
                arm="single",
                replicate=7,
            )
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["backbone_source"], "target")
            self.assertEqual(rows[0]["plddt"], 90.0)
            self.assertIsNone(rows[2]["plddt"])

            summary = module.summarize_group(rows, top_k=2)
            self.assertEqual(summary["n_designs"], 3)
            self.assertEqual(summary["n_plddt"], 2)
            self.assertAlmostEqual(summary["mean_plddt"], 85.0)
            self.assertAlmostEqual(summary["top2_mean_plddt"], 85.0)
            self.assertAlmostEqual(summary["plddt_pass_rate_85"], 0.5)
            self.assertAlmostEqual(summary["soluprot_pass_rate_0_5"], 2 / 3)
            self.assertAlmostEqual(summary["mean_pairwise_identity"], 8 / 12)

    def test_empty_summary_does_not_emit_placeholder_statistics(self):
        module = _load_module()
        self.assertEqual(module._paired_tests([]), [])

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "empty.csv"
            module.write_csv(out_path, [], fieldnames=["target", "arm", "mean_plddt"])

            self.assertEqual(out_path.read_text(encoding="utf-8"), "target,arm,mean_plddt\n")

    def test_existing_run_decision_resumes_incomplete_runs_only_when_requested(self):
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "abl_be_target_single_s1"
            run_dir.mkdir()
            status_path = run_dir / "status.json"

            status_path.write_text(
                json.dumps({"stage": "done", "state": "completed"}),
                encoding="utf-8",
            )
            self.assertEqual(
                module.existing_run_action(run_dir, force=False, resume_existing=True),
                "skip_completed",
            )

            status_path.write_text(
                json.dumps({"stage": "proteinmpnn_50", "state": "running"}),
                encoding="utf-8",
            )
            self.assertEqual(
                module.existing_run_action(run_dir, force=False, resume_existing=False),
                "skip_existing",
            )
            self.assertEqual(
                module.existing_run_action(run_dir, force=False, resume_existing=True),
                "resume",
            )
            self.assertEqual(module.resume_start_from({"stage": "proteinmpnn_50"}), "design")


if __name__ == "__main__":
    unittest.main()
