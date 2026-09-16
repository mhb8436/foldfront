import json
from pathlib import Path
from types import SimpleNamespace
import csv

import numpy as np

from pipeline_mcp import evolution
from pipeline_mcp.models import PipelineRequest
from pipeline_mcp.storage import init_run
from pipeline_mcp.tools import pipeline_request_from_args


class _FakeRunner:
    def __init__(self, output_root: Path):
        self.output_root = str(output_root)
        self.rosetta_relax = None
        self.pool_run_ids: list[str] = []

    def run(self, request: PipelineRequest, *, run_id: str | None = None):
        assert run_id is not None
        self.pool_run_ids.append(run_id)
        paths = init_run(self.output_root, run_id)
        tier_dir = paths.root / "tiers" / "30"
        tier_dir.mkdir(parents=True, exist_ok=True)
        round_no = len(self.pool_run_ids)
        records = []
        scores = {}
        for i in range(8):
            seq_id = f"r{round_no}_seq{i}"
            records.append(f">{seq_id}\nACDEFGHIK{i % 10}\n")
            scores[seq_id] = 0.9
        (tier_dir / "designs.fasta").write_text("".join(records), encoding="utf-8")
        (tier_dir / "soluprot.json").write_text(
            json.dumps({"scores": scores}), encoding="utf-8"
        )
        return SimpleNamespace(run_id=run_id)


def test_pipeline_request_defaults_to_paper_four_round_budget() -> None:
    assert PipelineRequest(target_fasta=">q\nACDE\n", target_pdb="").evolution_rounds == 4
    assert (
        PipelineRequest(target_fasta=">q\nACDE\n", target_pdb="").evolution_label_source
        == "experimental"
    )
    req = pipeline_request_from_args({"target_fasta": ">q\nACDE\n"})
    assert req.evolution_rounds == 4
    assert req.evolution_label_source == "experimental"


def test_run_evolution_executes_initial_training_then_topk_each_round(
    tmp_path, monkeypatch
) -> None:
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        device=lambda name: name,
    )
    monkeypatch.setattr(evolution, "torch", fake_torch)
    monkeypatch.setattr(evolution, "mlflow", None)
    monkeypatch.setattr(evolution.ncp_storage, "sync_outputs", lambda run_id: None)
    fake_module_file = tmp_path / "fakepkg" / "src" / "pipeline_mcp" / "evolution.py"
    monkeypatch.setattr(evolution, "__file__", str(fake_module_file))

    def fake_embeddings(sequences, device):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        values[:, 1] = values[:, 1] * 0.5
        return values

    monkeypatch.setattr(evolution, "get_esm_embeddings", fake_embeddings)

    def fake_af2_predict(runner, payload):
        fasta = str(payload["target_fasta"])
        seq_id = fasta.splitlines()[0].lstrip(">")
        eval_paths = init_run(runner.output_root, str(payload["run_id"]))
        (eval_paths.root / "af2" / seq_id).mkdir(parents=True, exist_ok=True)
        (eval_paths.root / "status.json").write_text(
            json.dumps({"run_id": payload["run_id"], "state": "completed"}),
            encoding="utf-8",
        )
        score = 70.0 + float(seq_id.rsplit("seq", 1)[-1])
        return {"summary": {"af2": {seq_id: {"best_plddt": score}}}}

    import pipeline_mcp.tools as tools

    monkeypatch.setattr(tools, "_run_af2_predict", fake_af2_predict)

    runner = _FakeRunner(tmp_path / "outputs")
    request = PipelineRequest(
        target_fasta=">q\nACDEFGHIK\n",
        target_pdb="",
        evolution_mode=True,
        evolution_label_source="in_silico_af2",
        evolution_initial_samples=3,
        evolution_oracle_samples=2,
        evolution_rounds=3,
        evolution_pool_size=12,
        soluprot_cutoff=0.1,
    )

    result = evolution.run_evolution(runner, request, "evo_multi")
    summary = json.loads((Path(result.output_dir) / "summary.json").read_text())

    assert len(runner.pool_run_ids) == 3
    subrun_manifest = json.loads(
        (Path(result.output_dir) / "evolution" / "subruns" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    subruns = subrun_manifest["subruns"]
    assert len(subruns) == 12
    assert sum(1 for item in subruns if item["category"] == "pool") == 3
    assert sum(1 for item in subruns if item["category"] == "oracle") == 9
    for pool_run_id in runner.pool_run_ids:
        assert not (Path(runner.output_root) / pool_run_id).exists()
        assert (
            Path(result.output_dir) / "evolution" / "subruns" / pool_run_id
        ).is_dir()
    assert summary["pool_statistics"]["rounds"] == 3
    assert summary["pool_statistics"]["oracle_train"] == 3
    assert summary["pool_statistics"]["oracle_top_k_per_round"] == 2
    assert summary["pool_statistics"]["oracle_total"] == 9
    assert len(summary["evaluated_samples"]) == 9

    phases = [row["phase"] for row in summary["evaluated_samples"]]
    assert phases.count("round_1_train") == 3
    assert phases.count("round_1_top_k") == 2
    assert phases.count("round_2_top_k") == 2
    assert phases.count("round_3_top_k") == 2


def test_run_evolution_experimental_without_labels_requests_wetlab_candidates(
    tmp_path, monkeypatch
) -> None:
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        device=lambda name: name,
    )
    monkeypatch.setattr(evolution, "torch", fake_torch)
    monkeypatch.setattr(evolution, "mlflow", None)
    monkeypatch.setattr(evolution.ncp_storage, "sync_outputs", lambda run_id: None)

    def fake_embeddings(sequences, device):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        return values

    monkeypatch.setattr(evolution, "get_esm_embeddings", fake_embeddings)

    import pipeline_mcp.tools as tools

    def fail_af2(*args, **kwargs):
        raise AssertionError("experimental evolution must not call AF2")

    monkeypatch.setattr(tools, "_run_af2_predict", fail_af2)

    runner = _FakeRunner(tmp_path / "outputs")
    request = PipelineRequest(
        target_fasta=">q\nACDEFGHIK\n",
        target_pdb="",
        evolution_mode=True,
        evolution_label_source="experimental",
        evolution_initial_samples=3,
        evolution_oracle_samples=2,
        evolution_rounds=1,
        evolution_pool_size=12,
        soluprot_cutoff=0.1,
    )

    result = evolution.run_evolution(runner, request, "evo_exp_request")
    root = Path(result.output_dir)
    summary = json.loads((root / "summary.json").read_text())
    request_csv = root / "evolution" / "experiment_request.csv"

    assert request_csv.exists()
    rows = list(csv.DictReader(request_csv.open()))
    assert len(rows) == 3
    assert summary["label_source"] == "experimental"
    assert summary["evolution_mode"] == "experimental-feedback-active-learning"
    assert summary["experimental_labels"]["count"] == 0
    assert len(summary["requested_experiments"]) == 3


def test_run_evolution_experimental_with_labels_recommends_next_candidates(
    tmp_path, monkeypatch
) -> None:
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        device=lambda name: name,
    )
    monkeypatch.setattr(evolution, "torch", fake_torch)
    monkeypatch.setattr(evolution, "mlflow", None)
    monkeypatch.setattr(evolution.ncp_storage, "sync_outputs", lambda run_id: None)

    def fake_embeddings(sequences, device):
        values = np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)
        return values

    monkeypatch.setattr(evolution, "get_esm_embeddings", fake_embeddings)

    runner = _FakeRunner(tmp_path / "outputs")
    source_paths = init_run(runner.output_root, "round_1")
    experiments = [
        {"sample_id": "r1_seq0", "metrics": {"activity": 0.2}, "result": "success"},
        {"candidate_id": "r1_seq1", "metric_name": "activity", "metric_value": 0.8, "result": "success"},
        {"sequence_id": "r1_seq2", "metrics": {"activity": 0.5}, "result": "success"},
    ]
    (source_paths.root / "experiments.jsonl").write_text(
        "\n".join(json.dumps(item) for item in experiments) + "\n",
        encoding="utf-8",
    )
    request = PipelineRequest(
        target_fasta=">q\nACDEFGHIK\n",
        target_pdb="",
        evolution_mode=True,
        evolution_label_source="experimental",
        evolution_experiment_source_run_id="round_1",
        evolution_objective_metric="activity",
        evolution_initial_samples=3,
        evolution_oracle_samples=2,
        evolution_rounds=1,
        evolution_pool_size=12,
        soluprot_cutoff=0.1,
    )

    result = evolution.run_evolution(runner, request, "evo_exp_rank")
    summary = json.loads((Path(result.output_dir) / "summary.json").read_text())

    assert summary["experimental_labels"]["count"] == 3
    assert len(summary["recommended_candidates"]) == 2
    assert all(item["id"] not in {"r1_seq0", "r1_seq1", "r1_seq2"} for item in summary["recommended_candidates"])


def test_experimental_labels_preserve_raw_value_and_score_minimize_metrics(
    tmp_path,
) -> None:
    output_root = tmp_path / "outputs"
    source_paths = init_run(output_root, "round_1")
    (source_paths.root / "experiments.jsonl").write_text(
        json.dumps(
            {
                "candidate_id": "seq_a",
                "metric_name": "ic50",
                "metric_value": 12.5,
                "metric_direction": "minimize",
                "result": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    labels = evolution._load_experimental_labels(str(output_root), ["round_1"], "ic50")

    assert labels["seq_a"]["value"] == 12.5
    assert labels["seq_a"]["metric_direction"] == "minimize"
    assert labels["seq_a"]["selection_score"] == -12.5


def test_run_evolution_experimental_reuses_labels_by_sequence_when_ids_change(
    tmp_path, monkeypatch
) -> None:
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        device=lambda name: name,
    )
    monkeypatch.setattr(evolution, "torch", fake_torch)
    monkeypatch.setattr(evolution, "mlflow", None)
    monkeypatch.setattr(evolution.ncp_storage, "sync_outputs", lambda run_id: None)

    def fake_embeddings(sequences, device):
        return np.arange(len(sequences) * 2, dtype=np.float64).reshape(len(sequences), 2)

    monkeypatch.setattr(evolution, "get_esm_embeddings", fake_embeddings)

    runner = _FakeRunner(tmp_path / "outputs")
    source_paths = init_run(runner.output_root, "round_1")
    request_csv = source_paths.root / "evolution" / "experiment_request.csv"
    request_csv.parent.mkdir(parents=True, exist_ok=True)
    request_csv.write_text(
        "rank,candidate_id,sequence,soluprot,selection_reason,predicted_objective,label_value\n"
        "1,old_seq0,ACDEFGHIK0,0.9,kmeans_bootstrap_experiment,,\n",
        encoding="utf-8",
    )
    (source_paths.root / "experiments.jsonl").write_text(
        json.dumps(
            {
                "candidate_id": "old_seq0",
                "metric_name": "activity",
                "metric_value": 0.8,
                "metric_direction": "maximize",
                "result": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    request = PipelineRequest(
        target_fasta=">q\nACDEFGHIK\n",
        target_pdb="",
        evolution_mode=True,
        evolution_label_source="experimental",
        evolution_experiment_source_run_id="round_1",
        evolution_objective_metric="activity",
        evolution_initial_samples=3,
        evolution_oracle_samples=2,
        evolution_rounds=1,
        evolution_pool_size=12,
        soluprot_cutoff=0.1,
    )

    result = evolution.run_evolution(runner, request, "evo_exp_sequence_match")
    summary = json.loads((Path(result.output_dir) / "summary.json").read_text())

    assert summary["experimental_labels"]["count"] == 1
    assert summary["experimental_labels"]["items"][0]["id"] == "r1_seq0"
    assert summary["experimental_labels"]["items"][0]["matched_by"] == "sequence"
