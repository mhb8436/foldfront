import json

from pipeline_mcp.runpod_metrics import get_runpod_metrics_store, latest_health


def test_latest_health_roundtrip(tmp_path):
    store = get_runpod_metrics_store(str(tmp_path))
    store.set_state(
        "health:ep1",
        json.dumps({"endpoint_id": "ep1", "queued": 3, "running": 1, "workers": 2,
                    "t": "2026-07-03T00:00:00Z"}),
    )
    h = latest_health(store, "ep1")
    assert h["queued"] == 3 and h["running"] == 1 and h["workers"] == 2


def test_latest_health_missing_returns_none(tmp_path):
    store = get_runpod_metrics_store(str(tmp_path))
    assert latest_health(store, "nope") is None
