"""Project a run out of MongoDB into the directory the original reads.

`db/legacy.py` moves a run directory into MongoDB. This is the other
direction, and it exists because the original's 62 tools were written against
the filesystem: `compare_runs`, `get_hit_list`, `generate_report` and the rest
all begin by resolving `<output_root>/<run_id>` and reading `request.json` and
`summary.json` out of it. A run started here has neither, so those tools
answer "run_id not found" for every run this platform has.

    MongoDB run  ──project──>  <output_root>/<run_id>/request.json
                                                      summary.json
                                                      status.json
                                                      events.jsonl

Four rules.

1. **Artifacts are not copied.** They are already on disk under the same run
   directory - `Artifact.path` is relative to the storage root and begins with
   the run id - so projecting only has to write the bookkeeping files that sit
   beside them. Copying would double the disk a run costs and give the two
   copies a chance to disagree.
2. **Only the four files are written, and only inside the run's own
   directory.** The projector never deletes and never touches anything a
   stage produced.
3. **The database wins.** A projection is rewritten from the current
   documents every time, so a stale file cannot outlive the run state it came
   from. `summary.json` is the exception: where a stage produced a real one,
   that is what is kept - see `_merge_summary`.
4. **Nothing is invented.** A field the run does not have is left out rather
   than filled with a plausible default. A tool reading it will say the value
   is missing, which is true, instead of reporting a number nobody computed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from foldfront.core.config import get_settings
from foldfront.db.legacy import (
    EVENTS_JSONL,
    REQUEST_JSON,
    STATUS_JSON,
    SUMMARY_JSON,
)
from foldfront.db.models import Run, RunStatus
from foldfront.db.repositories import Repos


class ProjectionError(RuntimeError):
    """The run could not be projected."""


#  PAUSED is ours; the original's status vocabulary has no word for a run
#  that is held open with nothing running. To a tool reading status.json, a
#  held run is one that has not finished, which is what `running` means.
_STATUS_OUT: dict[RunStatus, str] = {
    RunStatus.PENDING: "pending",
    RunStatus.RUNNING: "running",
    RunStatus.PAUSED: "running",
    RunStatus.SUCCEEDED: "succeeded",
    RunStatus.FAILED: "failed",
    RunStatus.CANCELLED: "cancelled",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class Projection:
    """Where a run was written, and what was written there."""

    run_id: str
    root: Path
    files: tuple[str, ...]
    stages: int
    events: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "files": list(self.files),
            "stages": self.stages,
            "events": self.events,
        }


class LegacyProjector:
    """Writes runs out in the original's shape, on demand."""

    def __init__(self, repos: Repos, *, output_root: str | Path | None = None) -> None:
        self.repos = repos
        self.root = Path(output_root or get_settings().output_root).resolve()

    def run_root(self, run_id: str) -> Path:
        """The run's directory, refusing any id that would escape the root.

        A run id reaches this from a tool argument, so it is attacker-shaped
        input even when the run turns out not to exist. Resolving first and
        comparing after also catches a symlink planted inside the root.
        """
        raw = str(run_id or "").strip()
        if not raw or raw in (".", "..") or "/" in raw or "\\" in raw:
            raise ProjectionError(f"실행 식별자로 쓸 수 없습니다: {run_id!r}")
        target = (self.root / raw).resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise ProjectionError(f"저장 루트 밖을 가리킵니다: {run_id!r}")
        return target

    async def project(self, run_id: str) -> Projection:
        """Write one run's bookkeeping files. Raises if there is no such run."""
        run = await self.repos.runs.get(run_id)
        if run is None:
            raise ProjectionError(f"실행을 찾지 못했습니다: {run_id}")
        return await self._write(run)

    async def project_if_present(self, run_id: str) -> Projection | None:
        """As `project`, but a missing run is not an error.

        Used on the tool path, where the original's own "run_id not found" is
        a better answer than a projection error: the tool is what the caller
        asked for, and it words the failure in its own terms.
        """
        try:
            return await self.project(run_id)
        except ProjectionError:
            return None

    async def _write(self, run: Run) -> Projection:
        root = self.run_root(run.run_id)
        root.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        _write_json(root / REQUEST_JSON, self._request(run))
        written.append(REQUEST_JSON)

        _write_json(root / STATUS_JSON, self._status(run))
        written.append(STATUS_JSON)

        summary = await self._merge_summary(run, root)
        _write_json(root / SUMMARY_JSON, summary)
        written.append(SUMMARY_JSON)

        events = await self._events(run.run_id, root)
        if events:
            written.append(EVENTS_JSONL)

        return Projection(
            run_id=run.run_id, root=root, files=tuple(written),
            stages=len(run.stages), events=events,
        )

    # ------------------------------------------------------------ the files

    def _request(self, run: Run) -> dict[str, Any]:
        """request.json, kept as it came in.

        The original's PipelineRequest has 127 fields and the migration keeps
        `request` verbatim for exactly this trip back. What is added are the
        few keys the original reads off the top of request.json and that this
        platform holds on the run document instead - and only where the
        request does not already carry them.
        """
        out = dict(run.request or {})
        for key, value in (
            ("run_id", run.run_id),
            ("project_id", run.project_id),
            ("round_id", run.round_id),
            ("workflow_id", run.workflow_id),
        ):
            if value is not None and key not in out:
                out[key] = value
        return out

    def _status(self, run: Run) -> dict[str, Any]:
        """status.json, in the list form the migration already reads back."""
        return {
            "run_id": run.run_id,
            "status": _STATUS_OUT[run.status],
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "error": run.error,
            "stages": [
                {
                    "name": st.name,
                    "status": _STATUS_OUT[st.status],
                    "started_at": _iso(st.started_at),
                    "finished_at": _iso(st.finished_at),
                    "model_id": st.model_id,
                    "model_version": st.model_version,
                    "endpoint_id": st.endpoint_id,
                    "request_hash": st.request_hash,
                    "attempt": st.attempt,
                    "error": st.error,
                    "metrics": dict(st.metrics),
                }
                for st in run.stages
            ],
        }

    async def _merge_summary(self, run: Run, root: Path) -> dict[str, Any]:
        """summary.json, preferring what a stage actually wrote.

        The analysis tools read `tiers` out of this file and then go looking
        for `tiers/<n>/soluprot.json` beside it. Those are produced by a model
        that really ran; this platform cannot synthesise them, and a `tiers`
        key built out of stage metrics would send `get_hit_list` looking for
        files that were never written.

        So where a real summary is already on disk it is kept, and only the
        run-level bookkeeping around it is refreshed. Where there is none, the
        file says what the run does know - its stages and their metrics - and
        omits `tiers` entirely, which is what makes the hit list report no
        rows rather than wrong ones.
        """
        existing: dict[str, Any] = {}
        path = root / SUMMARY_JSON
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                existing = loaded

        merged: dict[str, Any] = dict(existing)
        merged.update({
            "run_id": run.run_id,
            "status": _STATUS_OUT[run.status],
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version,
            "stages": [
                {"name": st.name, "status": _STATUS_OUT[st.status],
                 "model_id": st.model_id, "model_version": st.model_version,
                 "attempt": st.attempt, "metrics": dict(st.metrics)}
                for st in run.stages
            ],
            "metrics": {
                st.name: dict(st.metrics) for st in run.stages if st.metrics
            },
        })
        if run.error:
            merged["error"] = run.error

        #  Say so, rather than let a reader take a projected file for one a
        #  model wrote. `_mock` on a stage's metrics travels with it either
        #  way; this is about the file, not the numbers.
        merged["_projected_from"] = "foldfront"
        return merged

    async def _events(self, run_id: str, root: Path) -> int:
        """events.jsonl, rewritten whole.

        Appending would duplicate every earlier line on the second projection,
        and the file is the database's to state.
        """
        items = await self.repos.events.list(run_id, limit=10_000)
        if not items:
            return 0
        path = root / EVENTS_JSONL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for ev in items:
                f.write(json.dumps({
                    "seq": ev.seq,
                    "ts": _iso(ev.created_at),
                    "level": str(ev.level),
                    "stage": ev.stage,
                    "message": ev.message,
                    **({"payload": ev.payload} if ev.payload else {}),
                }, ensure_ascii=False, default=str) + "\n")
        return len(items)
