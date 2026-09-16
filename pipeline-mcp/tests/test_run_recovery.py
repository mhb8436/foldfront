from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

from pipeline_mcp.run_recovery import find_resumable_runs


def _mk_run(root, run_id, *, state, updated_ts, request=True, cancelled=False):
    d = root / run_id
    d.mkdir(parents=True)
    updated = datetime.fromtimestamp(updated_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    (d / "status.json").write_text(
        json.dumps({"run_id": run_id, "stage": "af2", "state": state, "updated_at": updated}),
        encoding="utf-8",
    )
    if request:
        (d / "request.json").write_text(json.dumps({"run_id": run_id, "target_fasta": ">a\nAAAA"}), encoding="utf-8")
    if cancelled:
        (d / "cancel.requested.json").write_text("{}", encoding="utf-8")


def test_resumes_only_recent_running_runs(tmp_path):
    now = 1_000_000.0
    _mk_run(tmp_path, "recent_running", state="running", updated_ts=now - 60)
    _mk_run(tmp_path, "old_running", state="running", updated_ts=now - 999_999)
    _mk_run(tmp_path, "completed", state="completed", updated_ts=now - 60)
    _mk_run(tmp_path, "failed", state="failed", updated_ts=now - 60)

    out = find_resumable_runs(tmp_path, now_ts=now, max_age_s=7200, max_runs=20)
    assert out == ["recent_running"]


def test_excludes_cancelled_and_missing_request(tmp_path):
    now = 1_000_000.0
    _mk_run(tmp_path, "cancelled_run", state="running", updated_ts=now - 60, cancelled=True)
    _mk_run(tmp_path, "no_request", state="running", updated_ts=now - 60, request=False)
    _mk_run(tmp_path, "good", state="running", updated_ts=now - 60)

    out = find_resumable_runs(tmp_path, now_ts=now, max_age_s=7200, max_runs=20)
    assert out == ["good"]


def test_sorted_recent_first_and_capped(tmp_path):
    now = 1_000_000.0
    for i in range(5):
        _mk_run(tmp_path, f"r{i}", state="running", updated_ts=now - i * 10)
    out = find_resumable_runs(tmp_path, now_ts=now, max_age_s=7200, max_runs=3)
    assert out == ["r0", "r1", "r2"]  # most recent first, capped to 3


def test_empty_or_missing_root(tmp_path):
    assert find_resumable_runs(tmp_path / "nope", now_ts=1.0, max_age_s=10, max_runs=5) == []
    assert find_resumable_runs(tmp_path, now_ts=1.0, max_age_s=10, max_runs=5) == []
