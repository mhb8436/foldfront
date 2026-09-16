from pipeline_mcp.queue_stats import stage_durations_from_events, bootstrap_from_events, QueueStatsStore
import json


def _ep(stage):
    return {"mmseqs_msa": "ep_msa", "bioemu": "ep_bio"}.get(stage)  # soluprot -> None


def test_stage_durations_from_events_pairs_running_to_completed():
    events = [
        {"stage": "mmseqs_msa", "state": "running", "updated_at": "2026-05-26 16:20:14"},
        {"stage": "mmseqs_msa", "state": "completed", "updated_at": "2026-05-26 16:21:05"},
        {"stage": "bioemu", "state": "running", "updated_at": "2026-05-26 16:21:05"},
        {"stage": "bioemu", "state": "running", "updated_at": "2026-05-26 16:21:06"},  # keep first
        {"stage": "bioemu", "state": "completed", "updated_at": "2026-05-26 16:25:44"},
        {"stage": "soluprot", "state": "running", "updated_at": "2026-05-26 16:25:44"},
        {"stage": "soluprot", "state": "completed", "updated_at": "2026-05-26 16:25:50"},
    ]
    out = sorted(stage_durations_from_events(events, _ep))
    assert out == [("ep_bio", 279.0), ("ep_msa", 51.0)]


def test_stage_durations_ignores_failed_and_missing_start():
    events = [
        {"stage": "bioemu", "state": "completed", "updated_at": "2026-05-26 16:25:44"},  # no running
        {"stage": "mmseqs_msa", "state": "running", "updated_at": "2026-05-26 16:20:14"},
        {"stage": "mmseqs_msa", "state": "failed", "updated_at": "2026-05-26 16:20:30"},  # not completed
    ]
    assert stage_durations_from_events(events, _ep) == []


def test_bootstrap_from_events_seeds_store(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    events = [
        {"stage": "mmseqs_msa", "state": "running", "updated_at": "2026-05-26 16:20:14"},
        {"stage": "mmseqs_msa", "state": "completed", "updated_at": "2026-05-26 16:21:05"},
    ]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    n = bootstrap_from_events(tmp_path, _ep)
    assert n == 1
    assert QueueStatsStore(tmp_path).avg_duration("ep_msa") == 51.0
