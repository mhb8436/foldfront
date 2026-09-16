import json

from pipeline_mcp.runpod_metrics import get_runpod_metrics_store
from pipeline_mcp.queue_stats import QueueStatsStore
from pipeline_mcp.tools import compute_queue_eta


def test_compute_queue_eta(tmp_path):
    store = get_runpod_metrics_store(str(tmp_path))
    store.set_state("health:epA", json.dumps({"queued": 2, "running": 1, "workers": 1, "t": "t"}))
    QueueStatsStore(tmp_path).record_duration("epA", 60.0)
    out = compute_queue_eta(
        output_root=tmp_path, store=store,
        remaining_stages=[{"stage": "af2", "endpoint_id": "epA"}],
    )
    s = out["per_stage"][0]
    assert s["stage"] == "af2" and s["queued"] == 2 and s["wait_s"] == 120.0
    assert out["est_finish_s"] == 180.0 and out["approximate"] is True


def test_compute_queue_eta_fallback_when_no_duration(tmp_path):
    store = get_runpod_metrics_store(str(tmp_path))
    store.set_state("health:epB", json.dumps({"queued": 5, "running": 0, "workers": 1, "t": "t"}))
    out = compute_queue_eta(
        output_root=tmp_path, store=store,
        remaining_stages=[{"stage": "msa", "endpoint_id": "epB"}],
    )
    s = out["per_stage"][0]
    assert s["queued"] == 5 and s["fallback"] is True and s["wait_s"] is None
    assert out["fallback"] is True


class _FakeRunpodCfg:
    mmseqs_endpoint_id = "ep_msa"
    rfd3_endpoint_id = None
    bioemu_endpoint_id = None
    proteinmpnn_endpoint_id = "ep_design"
    alphafold2_endpoint_id = "ep_af2"
    colabfold_endpoint_id = None


class _FakeCfg:
    runpod = _FakeRunpodCfg()


class _FakeRunner:
    def __init__(self, root):
        self.output_root = str(root)


def test_queue_eta_tool_no_run_id_reports_configured_stages(tmp_path, monkeypatch):
    import pipeline_mcp.tools as t
    store = get_runpod_metrics_store(str(tmp_path))
    store.set_state("health:ep_af2", json.dumps({"queued": 1, "running": 0, "workers": 1, "t": "t"}))
    QueueStatsStore(tmp_path).record_duration("ep_af2", 30.0)
    monkeypatch.setattr(t, "_load_config_for_eta", lambda: _FakeCfg())
    out = t._queue_eta_tool(_FakeRunner(tmp_path), {})
    stages = [s["stage"] for s in out["per_stage"]]
    # msa, design, af2 have endpoints; rfd3/bioemu/soluprot skipped; novelty maps to msa endpoint
    assert stages == ["msa", "design", "af2", "novelty"]
    af2 = next(s for s in out["per_stage"] if s["stage"] == "af2")
    assert af2["queued"] == 1 and af2["wait_s"] == 30.0 and af2["finish_s"] == 60.0
    assert out["current_stage"] is None and out["run_id"] is None
