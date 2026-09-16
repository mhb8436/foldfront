from pipeline_mcp.queue_stats import QueueStatsStore
from pipeline_mcp.queue_eta_hook import record_job_duration


def test_record_from_runpod_payload(tmp_path):
    record_job_duration(tmp_path, "ep1", {"status": "COMPLETED", "executionTime": 90000})
    assert QueueStatsStore(tmp_path).avg_duration("ep1") == 90.0


def test_ignores_incomplete_or_missing_time(tmp_path):
    record_job_duration(tmp_path, "ep1", {"status": "FAILED", "executionTime": 90000})
    record_job_duration(tmp_path, "ep1", {"status": "COMPLETED"})
    assert QueueStatsStore(tmp_path).avg_duration("ep1") is None
