from pipeline_mcp.queue_eta import estimate_stage_eta, estimate_run_eta


def test_stage_eta_basic():
    # 4 ahead, 2 workers, 60s each -> ceil(4/2)*60 = 120 wait; finish = 180
    r = estimate_stage_eta(jobs_ahead=4, workers=2, avg_duration_s=60.0)
    assert r == {"wait_s": 120.0, "finish_s": 180.0, "approximate": True, "fallback": False}


def test_stage_eta_zero_workers_treated_as_one():
    r = estimate_stage_eta(jobs_ahead=3, workers=0, avg_duration_s=10.0)
    assert r["wait_s"] == 30.0


def test_stage_eta_no_duration_is_fallback():
    r = estimate_stage_eta(jobs_ahead=2, workers=1, avg_duration_s=None)
    assert r["fallback"] is True and r["wait_s"] is None and r["finish_s"] is None


def test_run_eta_sums_remaining_stages():
    stages = [
        {"stage": "msa", "wait_s": 30.0, "finish_s": 60.0, "fallback": False},
        {"stage": "af2", "wait_s": 0.0, "finish_s": 120.0, "fallback": False},
    ]
    r = estimate_run_eta(stages)
    assert r["est_finish_s"] == 180.0 and r["fallback"] is False and r["approximate"] is True


def test_run_eta_partial_when_any_stage_fallback():
    stages = [
        {"stage": "msa", "wait_s": 30.0, "finish_s": 60.0, "fallback": False},
        {"stage": "af2", "wait_s": None, "finish_s": None, "fallback": True},
    ]
    r = estimate_run_eta(stages)
    assert r["fallback"] is True and r["est_finish_s"] == 60.0
