"""Record completed-stage durations for the queue-ETA feature.

Thin adapter between a RunPod job-result payload and the EWMA duration store.
Kept separate from the runner so it is unit-testable without RunPod.
"""
from __future__ import annotations

from pathlib import Path

from .queue_stats import QueueStatsStore


def record_job_duration(output_root: str | Path, endpoint_id: str, data: dict) -> None:
    """Persist the execution duration of a COMPLETED RunPod job.

    RunPod reports ``executionTime`` in milliseconds. Non-completed jobs or
    payloads without a duration are ignored.
    """
    if (data.get("status") or data.get("state")) != "COMPLETED":
        return
    ms = data.get("executionTime")
    if ms is None:
        return
    try:
        seconds = float(ms) / 1000.0
    except (TypeError, ValueError):
        return
    QueueStatsStore(output_root).record_duration(endpoint_id, seconds)
