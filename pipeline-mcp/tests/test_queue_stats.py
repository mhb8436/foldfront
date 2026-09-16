from pipeline_mcp.queue_stats import QueueStatsStore


def test_first_sample_bootstraps_average(tmp_path):
    s = QueueStatsStore(tmp_path)
    assert s.avg_duration("ep1") is None
    s.record_duration("ep1", 100.0)
    assert s.avg_duration("ep1") == 100.0


def test_ewma_update(tmp_path):
    s = QueueStatsStore(tmp_path)
    s.record_duration("ep1", 100.0)
    s.record_duration("ep1", 200.0)  # alpha=0.3 -> 0.7*100 + 0.3*200 = 130
    assert abs(s.avg_duration("ep1") - 130.0) < 1e-6


def test_ignores_nonpositive(tmp_path):
    s = QueueStatsStore(tmp_path)
    s.record_duration("ep1", 0)
    s.record_duration("ep1", -5)
    assert s.avg_duration("ep1") is None


def test_persists_across_instances(tmp_path):
    QueueStatsStore(tmp_path).record_duration("ep1", 100.0)
    assert QueueStatsStore(tmp_path).avg_duration("ep1") == 100.0
