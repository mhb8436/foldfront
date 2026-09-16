"""Per-endpoint EWMA of completed-job execution duration (seconds).

Single responsibility: persist and read a rolling average duration per RunPod
endpoint, backed by one JSON file under the output root. No RunPod knowledge.
Used by the queue-ETA feature to turn queue depth into a rough wait estimate.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

_ALPHA = 0.3  # EWMA weight for the newest sample


class QueueStatsStore:
    def __init__(self, output_root: str | Path) -> None:
        self.root = Path(output_root) / "_queue_stats"
        self.path = self.root / "durations.json"

    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, float]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    def record_duration(self, endpoint_id: str, seconds: float) -> None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        data = self._load()
        prev = data.get(endpoint_id)
        data[endpoint_id] = (
            seconds if prev is None else (1 - _ALPHA) * prev + _ALPHA * seconds
        )
        self._save(data)

    def avg_duration(self, endpoint_id: str) -> float | None:
        return self._load().get(endpoint_id)

    def is_empty(self) -> bool:
        return not self._load()


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def stage_durations_from_events(
    events: Iterable[dict], endpoint_for_stage: Callable[[str], str | None]
) -> list[tuple[str, float]]:
    """Pair each stage's first ``running`` with its ``completed`` event and return
    (endpoint_id, wall_seconds). Stages without an endpoint mapping are skipped."""
    starts: dict[str, datetime] = {}
    out: list[tuple[str, float]] = []
    for e in events:
        stage = e.get("stage")
        state = e.get("state")
        ts = _parse_ts(e.get("updated_at"))
        if not stage or ts is None:
            continue
        if state == "running":
            starts.setdefault(stage, ts)  # keep the first running timestamp
        elif state == "completed":
            start = starts.pop(stage, None)
            if start is None:
                continue
            eid = endpoint_for_stage(stage)
            if not eid:
                continue
            secs = (ts - start).total_seconds()
            if secs > 0:
                out.append((eid, float(secs)))
    return out


def bootstrap_from_events(
    output_root: str | Path,
    endpoint_for_stage: Callable[[str], str | None],
    max_runs: int = 300,
) -> int:
    """Seed the duration store from historical run events so ETA works before any
    new job completes. Returns the number of durations recorded."""
    root = Path(output_root)
    store = QueueStatsStore(output_root)
    files: list[Path] = []
    try:
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            f = d / "events.jsonl"
            if f.exists():
                files.append(f)
    except FileNotFoundError:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:max_runs]
    recorded = 0
    for f in reversed(files):  # oldest first so recent runs weigh most in the EWMA
        try:
            events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        except (ValueError, OSError):
            continue
        for eid, secs in stage_durations_from_events(events, endpoint_for_stage):
            store.record_duration(eid, secs)
            recorded += 1
    return recorded
