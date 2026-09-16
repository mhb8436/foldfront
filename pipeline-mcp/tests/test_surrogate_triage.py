import base64
import gzip
import json
from pathlib import Path

import numpy as np

from pipeline_mcp import pipeline
from pipeline_mcp.models import PipelineRequest
from pipeline_mcp.models import SequenceRecord
from pipeline_mcp.pipeline import PipelineRunner
from pipeline_mcp.tools import ToolDispatcher
from pipeline_mcp.tools import pipeline_request_from_args


def _pdb_9mer() -> str:
    lines = []
    residues = ["ALA", "CYS", "ASP", "GLU", "PHE", "GLY", "HIS", "ILE", "LYS"]
    for idx, resname in enumerate(residues, start=1):
        lines.append(
            f"ATOM  {idx:5d}  CA  {resname} A{idx:4d}    "
            f"{float(idx - 1):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 20.00           C"
        )
    lines.append("END")
    return "\n".join(lines) + "\n"


class _FakeMmseqs:
    def search(self, **kwargs):
        fasta = ">query\nACDEFGHIK\n>hit1\nACDEFGHIK\n>hit2\nACDEFGHIA\n"
        return {
            "tsv": "",
            "a3m_gz_b64": base64.b64encode(gzip.compress(fasta.encode())).decode(),
        }


class _FakeProteinMPNN:
    def design(self, **kwargs):
        count = int(kwargs.get("num_seq_per_target") or 1)
        query = "ACDEFGHIK"
        native = SequenceRecord(id="native", header="native", sequence=query)
        samples = [
            SequenceRecord(
                id=f"s{i + 1}",
                header=f"sample={i + 1}",
                sequence=query[:-1] + "ACDEFGHIK"[i % 9],
            )
            for i in range(count)
        ]
        return native, samples, {}


class _FakeAf2:
    def predict(self, batch_inputs, **kwargs):
        return {
            rec.id: {
                "best_plddt": 70.0 + idx,
                "best_model": "fake",
                "ranking_debug": {},
                "ranked_0_pdb": "",
            }
            for idx, rec in enumerate(batch_inputs)
        }


def test_pipeline_request_from_args_preserves_surrogate_triage_options() -> None:
    req = pipeline_request_from_args(
        {
            "target_fasta": ">q1\nACDEFGHIK\n",
            "surrogate_triage_enabled": True,
            "surrogate_triage_scope": "pooled_tiers",
            "surrogate_triage_initial_samples": 12,
            "surrogate_triage_top_k": 7,
            "surrogate_triage_model": "ridge",
        }
    )

    assert req.surrogate_triage_enabled is True
    assert req.surrogate_triage_scope == "pooled_tiers"
    assert req.surrogate_triage_initial_samples == 12
    assert req.surrogate_triage_top_k == 7
    assert req.surrogate_triage_model == "ridge"


def test_pipeline_request_from_args_accepts_multiple_surrogate_triage_models() -> None:
    req = pipeline_request_from_args(
        {
            "target_fasta": ">q1\nACDEFGHIK\n",
            "surrogate_triage_enabled": True,
            "surrogate_triage_model": ["rf", "ridge"],
        }
    )

    assert req.surrogate_triage_model == ["rf", "ridge"]


def test_pipeline_request_from_args_preserves_surrogate_auto_cv_options() -> None:
    req = pipeline_request_from_args(
        {
            "target_fasta": ">q1\nACDEFGHIK\n",
            "surrogate_triage_enabled": True,
            "surrogate_triage_model": "auto",
            "surrogate_triage_comparator_models": ["rf", "ridge"],
            "surrogate_triage_ensemble_models": "rf,ridge",
            "surrogate_triage_cv_folds": 3,
        }
    )

    assert req.surrogate_triage_model == "auto"
    assert req.surrogate_triage_comparator_models == ["rf", "ridge"]
    assert req.surrogate_triage_ensemble_models == ["rf", "ridge"]
    assert req.surrogate_triage_cv_folds == 3


def test_pipeline_request_from_args_leaves_rank_ensemble_disabled_by_default() -> None:
    req = pipeline_request_from_args(
        {
            "target_fasta": ">q1\nACDEFGHIK\n",
            "surrogate_triage_enabled": True,
            "surrogate_triage_model": "auto",
        }
    )

    assert req.surrogate_triage_comparator_models == ["rf", "ridge", "lightgbm", "xgboost"]
    assert req.surrogate_triage_ensemble_models == []


def test_surrogate_triage_embeddings_use_configured_provider() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.sequences = None

        def embed(self, sequences):  # type: ignore[no-untyped-def]
            self.sequences = list(sequences)
            return np.ones((len(sequences), 3), dtype=np.float32)

    provider = _Provider()
    embeddings = pipeline._surrogate_triage_embeddings(["ACD", "EFG"], provider=provider)

    assert provider.sequences == ["ACD", "EFG"]
    assert embeddings.shape == (2, 3)


def test_pipeline_surrogate_triage_limits_af2_to_training_plus_topk(
    tmp_path, monkeypatch
) -> None:
    def fake_embeddings(sequences, device=None, provider=None):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        values[:, 1] = values[:, 1] * 0.25
        return values

    monkeypatch.setattr(pipeline, "_surrogate_triage_embeddings", fake_embeddings)

    runner = PipelineRunner(
        output_root=str(tmp_path),
        mmseqs=_FakeMmseqs(),
        proteinmpnn=_FakeProteinMPNN(),
        soluprot=None,
        af2=_FakeAf2(),
    )
    req = PipelineRequest(
        target_fasta=">q1\nACDEFGHIK\n",
        target_pdb=_pdb_9mer(),
        dry_run=False,
        conservation_tiers=[0.3],
        num_seq_per_tier=8,
        soluprot_cutoff=0.0,
        rfd3_use=False,
        bioemu_use=False,
        surrogate_triage_enabled=True,
        surrogate_triage_initial_samples=3,
        surrogate_triage_top_k=2,
        surrogate_triage_model="rf",
        af2_top_k=0,
    )

    result = runner.run(req, run_id="surrogate_triage_dry")
    tier_dir = Path(result.output_dir) / "tiers" / "30"
    af2_scores = json.loads((tier_dir / "af2_scores.json").read_text())

    assert af2_scores["surrogate_triage"]["enabled"] is True
    assert af2_scores["surrogate_triage"]["initial_samples"] == 3
    assert af2_scores["surrogate_triage"]["top_k"] == 2
    assert af2_scores["candidate_count_before_budget"] > 5
    assert af2_scores["candidate_count_after_budget"] == 5
    assert af2_scores["candidate_budget_applied"] is True
    assert af2_scores.get("recovered") is None
    assert len(af2_scores["candidate_ids"]) == 5
    assert len(af2_scores["surrogate_triage"]["training_ids"]) == 3
    assert len(af2_scores["surrogate_triage"]["selected_top_ids"]) == 2


def test_pipeline_pooled_tier_surrogate_triage_uses_one_global_af2_budget(
    tmp_path, monkeypatch
) -> None:
    def fake_embeddings(sequences, device=None, provider=None):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        values[:, 1] = values[:, 1] * 0.25
        return values

    monkeypatch.setattr(pipeline, "_surrogate_triage_embeddings", fake_embeddings)

    runner = PipelineRunner(
        output_root=str(tmp_path),
        mmseqs=_FakeMmseqs(),
        proteinmpnn=_FakeProteinMPNN(),
        soluprot=None,
        af2=_FakeAf2(),
    )
    req = PipelineRequest(
        target_fasta=">q1\nACDEFGHIK\n",
        target_pdb=_pdb_9mer(),
        dry_run=False,
        conservation_tiers=[0.3, 0.5, 0.7],
        num_seq_per_tier=8,
        soluprot_cutoff=0.0,
        rfd3_use=False,
        bioemu_use=False,
        surrogate_triage_enabled=True,
        surrogate_triage_scope="pooled_tiers",
        surrogate_triage_initial_samples=3,
        surrogate_triage_top_k=2,
        surrogate_triage_model="rf",
        af2_top_k=0,
        novelty_enabled=False,
    )

    result = runner.run(req, run_id="surrogate_triage_pooled_tiers")
    run_root = Path(result.output_dir)
    pooled_selection = json.loads(
        (run_root / "surrogate_triage" / "model_selection.json").read_text()
    )
    tier_payloads = [
        json.loads((run_root / "tiers" / tier / "af2_scores.json").read_text())
        for tier in ("30", "50", "70")
    ]
    evaluated_ids = [
        seq_id
        for payload in tier_payloads
        for seq_id in payload["surrogate_triage"]["evaluated_ids"]
    ]

    assert pooled_selection["scope"] == "pooled_tiers"
    assert pooled_selection["candidate_count_before_triage"] == 24
    assert pooled_selection["expected_af2_calls"] == 5
    assert sum(payload["candidate_count_after_budget"] for payload in tier_payloads) == 5
    assert len(evaluated_ids) == 5
    assert all(
        payload["surrogate_triage"]["scope"] == "pooled_tiers"
        for payload in tier_payloads
    )

    report = ToolDispatcher(runner).call_tool(
        "pipeline.generate_report", {"run_id": "surrogate_triage_pooled_tiers"}
    )
    assert "Pooled tiers" in report["report"]
    assert "surrogate_triage/model_comparison.svg" in report["report"]


def test_pipeline_surrogate_triage_rank_mean_for_multiple_models(
    tmp_path, monkeypatch
) -> None:
    def fake_embeddings(sequences, device=None, provider=None):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        values[:, 1] = values[:, 1] * 0.25
        return values

    monkeypatch.setattr(pipeline, "_surrogate_triage_embeddings", fake_embeddings)

    runner = PipelineRunner(
        output_root=str(tmp_path),
        mmseqs=_FakeMmseqs(),
        proteinmpnn=_FakeProteinMPNN(),
        soluprot=None,
        af2=_FakeAf2(),
    )
    req = PipelineRequest(
        target_fasta=">q1\nACDEFGHIK\n",
        target_pdb=_pdb_9mer(),
        dry_run=False,
        conservation_tiers=[0.3],
        num_seq_per_tier=8,
        soluprot_cutoff=0.0,
        rfd3_use=False,
        bioemu_use=False,
        surrogate_triage_enabled=True,
        surrogate_triage_initial_samples=3,
        surrogate_triage_top_k=2,
        surrogate_triage_model=["rf", "ridge"],
        af2_top_k=0,
    )

    result = runner.run(req, run_id="surrogate_triage_multi_model")
    tier_dir = Path(result.output_dir) / "tiers" / "30"
    af2_scores = json.loads((tier_dir / "af2_scores.json").read_text())
    triage = af2_scores["surrogate_triage"]

    assert triage["models"] == ["rf", "ridge"]
    assert triage["selection_strategy"] == "rank_mean_ensemble"
    assert triage["candidate_count_after_budget"] == 5
    assert len(triage["training_ids"]) == 3
    assert len(triage["selected_top_ids"]) == 2


def test_pipeline_surrogate_triage_auto_cv_exports_analysis_artifacts(
    tmp_path, monkeypatch
) -> None:
    def fake_embeddings(sequences, device=None, provider=None):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        values[:, 1] = values[:, 1] * 0.25
        return values

    monkeypatch.setattr(pipeline, "_surrogate_triage_embeddings", fake_embeddings)

    runner = PipelineRunner(
        output_root=str(tmp_path),
        mmseqs=_FakeMmseqs(),
        proteinmpnn=_FakeProteinMPNN(),
        soluprot=None,
        af2=_FakeAf2(),
    )
    req = PipelineRequest(
        target_fasta=">q1\nACDEFGHIK\n",
        target_pdb=_pdb_9mer(),
        dry_run=False,
        conservation_tiers=[0.3],
        num_seq_per_tier=8,
        soluprot_cutoff=0.0,
        rfd3_use=False,
        bioemu_use=False,
        surrogate_triage_enabled=True,
        surrogate_triage_initial_samples=3,
        surrogate_triage_top_k=2,
        surrogate_triage_model="auto",
        surrogate_triage_comparator_models=["rf", "ridge"],
        surrogate_triage_ensemble_models=["rf", "ridge"],
        surrogate_triage_cv_folds=3,
        af2_top_k=0,
    )

    result = runner.run(req, run_id="surrogate_triage_auto_cv")
    tier_dir = Path(result.output_dir) / "tiers" / "30"
    triage_dir = tier_dir / "surrogate_triage"
    af2_scores = json.loads((tier_dir / "af2_scores.json").read_text())
    triage = af2_scores["surrogate_triage"]

    assert triage["selection_strategy"] == "auto_cv"
    assert triage["selected_policy"]
    assert triage["candidate_count_after_budget"] == 5
    assert len(triage["training_ids"]) == 3
    assert len(triage["selected_top_ids"]) == 2
    assert (triage_dir / "model_selection.json").exists()
    assert (triage_dir / "cv_metrics.csv").exists()
    assert (triage_dir / "model_predictions.csv").exists()
    assert (triage_dir / "model_comparison.svg").exists()
    assert (triage_dir / "acquired_topk.csv").exists()
    assert (triage_dir / "topk_overlap.csv").exists()
    assert (triage_dir / "feature_importance.csv").exists()
    assert any((triage_dir / "models").glob("*.pkl"))

    report = ToolDispatcher(runner).call_tool(
        "pipeline.generate_report", {"run_id": "surrogate_triage_auto_cv"}
    )
    assert "Surrogate Triage" in report["report"]
    assert "selected policy" in report["report"]
    assert "model_comparison.svg" in report["report"]
    assert "대리모델 선별" in report["report_ko"]
