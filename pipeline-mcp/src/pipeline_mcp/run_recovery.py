"""Resume runs orphaned by a pipeline-server restart.

Heavy stages execute on external workers (RunPod / GPU HTTP) keyed by a job_id
that survives a restart, but the in-process loop that polls the job and advances
``status.json`` dies when the server restarts — leaving the run stuck at
``state="running"`` while the worker keeps going. On startup we re-invoke
``pipeline.run`` for recently-interrupted runs by replaying their saved
``request.json``; the runner re-attaches to the recorded job_id (resume) and
reuses cached artifacts instead of recomputing.

Safety guards: only runs in state ``running``, updated within
``PIPELINE_AUTO_RESUME_MAX_AGE_S`` (default 2h), without a cancel marker, with a
saved ``request.json``; capped at ``PIPELINE_AUTO_RESUME_MAX_RUNS`` (default 20).
Disabled by setting ``PIPELINE_AUTO_RESUME_ON_STARTUP=0``.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_timestamp(value: Any) -> float | None:
    """Parse status.json updated_at ('YYYY-MM-DD HH:MM:SS' or ISO, treated as UTC)."""
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def find_resumable_runs(
    output_root: str | Path,
    *,
    now_ts: float,
    max_age_s: int,
    max_runs: int,
) -> list[str]:
    """Return run_ids that look interrupted-mid-flight and safe to resume.

    A run qualifies when its ``status.json`` has ``state == "running"``, was
    updated within ``max_age_s`` of ``now_ts``, has a saved ``request.json``, and
    has no ``cancel.requested.json`` marker. Most-recently-updated first, capped.
    """
    root = Path(str(output_root or "")).expanduser()
    if not root.is_dir():
        return []
    candidates: list[tuple[float, str]] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        status_path = run_dir / "status.json"
        request_path = run_dir / "request.json"
        if not status_path.is_file() or not request_path.is_file():
            continue
        if (run_dir / "cancel.requested.json").exists():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(status, dict):
            continue
        if str(status.get("state") or "").strip().lower() != "running":
            continue
        updated = _parse_timestamp(status.get("updated_at"))
        if updated is None or (now_ts - updated) > max_age_s:
            continue
        candidates.append((updated, run_dir.name))
    candidates.sort(reverse=True)
    return [name for _updated, name in candidates[: max(0, max_runs)]]


def _clear_running_state(status_path: Path) -> None:
    """Flip an orphaned run's ``state`` out of "running" so pipeline.run's
    duplicate-job guard allows the resume. Safe because the run is orphaned
    (no live process is writing status.json)."""
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(status, dict) and str(status.get("state") or "").strip().lower() == "running":
        status["state"] = "interrupted"
        try:
            status_path.write_text(json.dumps(status), encoding="utf-8")
        except OSError:
            pass


def _resume_one(dispatcher: Any, output_root: Path, run_id: str) -> None:
    try:
        run_dir = output_root / run_id
        payload = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            print(f"[run-recovery] skip {run_id}: request.json is not an object", flush=True)
            return
        # pipeline.run refuses a run whose status is still "running" (dup guard),
        # which is exactly the orphaned state — clear it before replaying.
        _clear_running_state(run_dir / "status.json")
        payload["run_id"] = run_id
        print(f"[run-recovery] resuming run_id={run_id}", flush=True)
        dispatcher.call_tool("pipeline.run", payload)
        print(f"[run-recovery] finished resume run_id={run_id}", flush=True)
    except Exception as exc:  # noqa: BLE001 - one run must not break the others
        print(f"[run-recovery] resume failed run_id={run_id}: {exc}", flush=True)


def start_run_recovery(dispatcher: Any, output_root: str | Path) -> bool:
    """Spawn a background daemon that resumes orphaned runs. Returns True if started."""
    if not _env_flag("PIPELINE_AUTO_RESUME_ON_STARTUP", True):
        return False
    if dispatcher is None:
        return False
    root = Path(str(output_root or "")).expanduser()
    max_age_s = _env_int("PIPELINE_AUTO_RESUME_MAX_AGE_S", 7200)
    max_runs = _env_int("PIPELINE_AUTO_RESUME_MAX_RUNS", 20)

    def _worker() -> None:
        try:
            run_ids = find_resumable_runs(
                root, now_ts=time.time(), max_age_s=max_age_s, max_runs=max_runs
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[run-recovery] scan failed: {exc}", flush=True)
            return
        if not run_ids:
            print("[run-recovery] no interrupted runs to resume", flush=True)
            return
        print(f"[run-recovery] resuming {len(run_ids)} interrupted run(s): {run_ids}", flush=True)
        for run_id in run_ids:
            # Each resume blocks until its stage completes; give each its own thread.
            threading.Thread(
                target=_resume_one, args=(dispatcher, root, run_id), daemon=True
            ).start()
            time.sleep(1.0)  # stagger so we don't hammer workers at once

    threading.Thread(target=_worker, daemon=True).start()
    return True
