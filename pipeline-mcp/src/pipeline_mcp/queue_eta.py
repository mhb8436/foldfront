"""Stateless queue-ETA math.

Pure functions turning (jobs_ahead, workers, avg_duration) into approximate
wait/finish seconds, and summing per-stage estimates into a whole-run estimate.
No I/O, no RunPod. All estimates are explicitly approximate; missing duration
data degrades to a fallback (counts-only) with no time.
"""
from __future__ import annotations

import math
from typing import Any


def estimate_stage_eta(
    *, jobs_ahead: int, workers: int, avg_duration_s: float | None
) -> dict[str, Any]:
    if avg_duration_s is None or avg_duration_s <= 0:
        return {"wait_s": None, "finish_s": None, "approximate": True, "fallback": True}
    ahead = max(int(jobs_ahead), 0)
    w = max(int(workers), 1)
    wait_s = math.ceil(ahead / w) * float(avg_duration_s)
    return {
        "wait_s": wait_s,
        "finish_s": wait_s + float(avg_duration_s),
        "approximate": True,
        "fallback": False,
    }


def estimate_run_eta(stages: list[dict[str, Any]]) -> dict[str, Any]:
    known = [s for s in stages if not s.get("fallback") and s.get("finish_s") is not None]
    any_fallback = any(s.get("fallback") for s in stages)
    est_finish = sum(float(s["finish_s"]) for s in known) if known else None
    return {
        "est_finish_s": est_finish,
        "approximate": True,
        "fallback": bool(any_fallback),
        "per_stage": stages,
    }
