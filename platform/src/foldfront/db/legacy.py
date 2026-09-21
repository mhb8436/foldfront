"""Migration of existing result data.

The original stores runs on the filesystem.

    <PIPELINE_OUTPUT_ROOT>/
      <run_id>/
        request.json  summary.json  status.json
        events.jsonl  orchestration_trace.jsonl
        feedback.jsonl  experiments.jsonl
        <artifacts, per stage>
      workspace/projects/<project_id>/
        project.json
        rounds/<round_id>.json

This reads that layout and moves it into MongoDB under three rules.

1. **Nothing is deleted.** The migration only reads, so a bad run of it costs
   nothing but the time to drop the collections and try again.
2. **No field is renamed.** request.json is preserved whole.
3. **Running it twice changes nothing.** The same run is never inserted twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from foldfront.db.models import (
    Artifact,
    Experiment,
    Feedback,
    Project,
    Round,
    Run,
    RunEvent,
    RunStatus,
    StageState,
)
from foldfront.db.repositories import Repos

#  The filenames the original writes. Not ours to rename.
REQUEST_JSON = "request.json"
SUMMARY_JSON = "summary.json"
STATUS_JSON = "status.json"
EVENTS_JSONL = "events.jsonl"
FEEDBACK_JSONL = "feedback.jsonl"
EXPERIMENTS_JSONL = "experiments.jsonl"

#  Extension to artifact kind
KIND_BY_SUFFIX = {
    ".pdb": "pdb", ".cif": "pdb", ".fasta": "fasta", ".fa": "fasta",
    ".a3m": "a3m", ".json": "json", ".jsonl": "json", ".svg": "svg",
    ".tsv": "tsv", ".csv": "tsv", ".log": "log", ".md": "report",
    ".html": "report", ".pdf": "report", ".png": "image", ".zip": "archive",
}

#  Bookkeeping files at the top of a run directory; not artifacts
CONTROL_FILES = {
    REQUEST_JSON, SUMMARY_JSON, STATUS_JSON, EVENTS_JSONL,
    FEEDBACK_JSONL, EXPERIMENTS_JSONL, "orchestration_trace.jsonl",
}


@dataclass
class MigrationReport:
    """Counts what moved, so a migration can be checked afterwards."""

    runs: int = 0
    runs_skipped: int = 0
    events: int = 0
    artifacts: int = 0
    feedback: int = 0
    experiments: int = 0
    projects: int = 0
    rounds: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs, "runs_skipped": self.runs_skipped,
            "events": self.events, "artifacts": self.artifacts,
            "feedback": self.feedback, "experiments": self.experiments,
            "projects": self.projects, "rounds": self.rounds,
            "errors": self.errors,
        }


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _to_datetime(value: Any) -> datetime | None:
    """The original mixes ISO strings and epoch seconds. Accept both."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _map_status(raw: Any) -> RunStatus:
    """Carry over the original status strings. An unrecognised one becomes
    pending rather than failed - calling something a failure on the strength of
    not recognising it would be worse than waiting."""
    text = str(raw or "").strip().lower()
    table = {
        "succeeded": RunStatus.SUCCEEDED, "success": RunStatus.SUCCEEDED,
        "completed": RunStatus.SUCCEEDED, "done": RunStatus.SUCCEEDED, "ok": RunStatus.SUCCEEDED,
        "failed": RunStatus.FAILED, "error": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED, "canceled": RunStatus.CANCELLED,
        "running": RunStatus.RUNNING, "in_progress": RunStatus.RUNNING,
        "pending": RunStatus.PENDING, "queued": RunStatus.PENDING,
    }
    return table.get(text, RunStatus.PENDING)


def _kind_of(path: Path) -> str:
    return KIND_BY_SUFFIX.get(path.suffix.lower(), "other")


def _stage_of(rel: Path) -> str | None:
    """Infer which stage produced an artifact from its top directory."""
    parts = rel.parts
    return parts[0] if len(parts) > 1 else None


def looks_like_run_dir(path: Path) -> bool:
    return path.is_dir() and (path / REQUEST_JSON).exists()


class LegacyMigrator:
    """Move the original output directory into MongoDB."""

    def __init__(self, repos: Repos, *, register_artifacts: bool = True) -> None:
        self.repos = repos
        self.register_artifacts = register_artifacts

    async def migrate_root(
        self, output_root: str | Path, *, limit: int | None = None, overwrite: bool = False
    ) -> MigrationReport:
        root = Path(output_root).expanduser().resolve()
        report = MigrationReport()
        if not root.exists():
            report.errors.append(f"경로가 없다: {root}")
            return report

        await self._migrate_projects(root, report)

        count = 0
        for child in sorted(root.iterdir()):
            if not looks_like_run_dir(child):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            try:
                await self._migrate_run(child, report, overwrite=overwrite)
            except Exception as exc:  # 한 run 이 깨져도 나머지는 옮긴다
                report.errors.append(f"{child.name}: {exc}")
        return report

    # ---------------------------------------------------------- run

    async def _migrate_run(
        self, run_dir: Path, report: MigrationReport, *, overwrite: bool
    ) -> None:
        run_id = run_dir.name

        if not overwrite and await self.repos.runs.get(run_id) is not None:
            report.runs_skipped += 1
            return

        request = _read_json(run_dir / REQUEST_JSON) or {}
        status_doc = _read_json(run_dir / STATUS_JSON) or {}
        summary = _read_json(run_dir / SUMMARY_JSON) or {}

        if not isinstance(request, dict):
            request = {"_raw": request}

        run = Run(
            run_id=run_id,
            status=_map_status(status_doc.get("status") or summary.get("status")),
            request=request,
            project_id=request.get("project_id"),
            round_id=request.get("round_id"),
            target_fasta_name=_basename(request.get("target_fasta")),
            design_chains=_as_list(request.get("design_chains")),
            conservation_tiers=_as_float_list(request.get("conservation_tiers")),
            stages=self._stages_from(status_doc, summary),
            started_at=_to_datetime(status_doc.get("started_at") or summary.get("started_at")),
            finished_at=_to_datetime(status_doc.get("finished_at") or summary.get("finished_at")),
            error=status_doc.get("error") or summary.get("error"),
        )
        #  Modification time stands in for creation time; it is the best the
        #  filesystem kept of when the artifact was produced
        created = _to_datetime((run_dir / REQUEST_JSON).stat().st_mtime)
        if created:
            run.created_at = created

        if overwrite:
            await self.repos.runs.col.delete_one({"run_id": run_id})
        await self.repos.runs.create(run)
        report.runs += 1

        await self._migrate_events(run_dir, run_id, report)
        await self._migrate_records(run_dir, run_id, report)
        if self.register_artifacts:
            await self._migrate_artifacts(run_dir, run_id, report)

    def _stages_from(
        self, status_doc: dict[str, Any], summary: dict[str, Any]
    ) -> list[StageState]:
        """status.json spells stages differently across versions of the
        original. Accept both the mapping and the list form."""
        raw = status_doc.get("stages") or summary.get("stages") or {}
        stages: list[StageState] = []

        if isinstance(raw, dict):
            items = raw.items()
        elif isinstance(raw, list):
            items = [(s.get("name"), s) for s in raw if isinstance(s, dict)]
        else:
            return stages

        for name, body in items:
            if not name:
                continue
            body = body if isinstance(body, dict) else {"status": body}
            stages.append(StageState(
                name=str(name),
                status=_map_status(body.get("status")),
                started_at=_to_datetime(body.get("started_at")),
                finished_at=_to_datetime(body.get("finished_at")),
                model_id=body.get("model_id") or body.get("model"),
                model_version=body.get("model_version"),
                endpoint_id=body.get("endpoint_id"),
                request_hash=body.get("request_hash") or body.get("hash"),
                error=body.get("error"),
                metrics=body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
            ))
        return stages

    async def _migrate_events(
        self, run_dir: Path, run_id: str, report: MigrationReport
    ) -> None:
        docs: list[dict[str, Any]] = []
        for seq, obj in enumerate(_read_jsonl(run_dir / EVENTS_JSONL), start=1):
            ev = RunEvent(
                run_id=run_id,
                seq=seq,
                level=_level_of(obj.get("level")),
                stage=obj.get("stage") or obj.get("step"),
                message=str(obj.get("message") or obj.get("event") or obj.get("msg") or ""),
                payload={k: v for k, v in obj.items()
                         if k not in {"level", "stage", "step", "message", "event", "msg"}},
            )
            ts = _to_datetime(obj.get("ts") or obj.get("timestamp") or obj.get("time"))
            if ts:
                ev.created_at = ts
            docs.append(ev.model_dump())

        if docs:
            await self.repos.events.col.delete_many({"run_id": run_id})
            await self.repos.events.col.insert_many(docs)
            #  The run's event counter has to start where these end, or the
            #  first new event collides with the last migrated one.
            await self.repos.runs.col.update_one(
                {"run_id": run_id}, {"$max": {"event_seq": len(docs)}}
            )
            report.events += len(docs)

    async def _migrate_records(
        self, run_dir: Path, run_id: str, report: MigrationReport
    ) -> None:
        fb = [
            Feedback(
                run_id=run_id,
                subject_id=o.get("subject_id") or o.get("sequence_id") or o.get("id"),
                verdict=_verdict_of(o.get("verdict") or o.get("rating")),
                score=_as_float(o.get("score")),
                comment=o.get("comment") or o.get("note"),
                author_id=o.get("author_id") or o.get("user"),
            ).model_dump()
            for o in _read_jsonl(run_dir / FEEDBACK_JSONL)
        ]
        if fb:
            await self.repos.feedback.col.delete_many({"run_id": run_id})
            await self.repos.feedback.col.insert_many(fb)
            report.feedback += len(fb)

        ex = [
            Experiment(
                run_id=run_id,
                subject_id=o.get("subject_id") or o.get("sequence_id") or o.get("id"),
                metric=o.get("metric"),
                value=_as_float(o.get("value")),
                unit=o.get("unit"),
                protocol=o.get("protocol"),
                note=o.get("note") or o.get("comment"),
                author_id=o.get("author_id") or o.get("user"),
            ).model_dump()
            for o in _read_jsonl(run_dir / EXPERIMENTS_JSONL)
        ]
        if ex:
            await self.repos.experiments.col.delete_many({"run_id": run_id})
            await self.repos.experiments.col.insert_many(ex)
            report.experiments += len(ex)

    async def _migrate_artifacts(
        self, run_dir: Path, run_id: str, report: MigrationReport
    ) -> None:
        docs: list[dict[str, Any]] = []
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(run_dir)
            if rel.name in CONTROL_FILES and len(rel.parts) == 1:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            docs.append(Artifact(
                run_id=run_id,
                stage=_stage_of(rel),
                #  Relative to the storage root, not to the run directory.
                #  That is what serves a download and what the projector
                #  writes beside; a path relative to the run would be looked
                #  for one level too high and never found.
                path=str(Path(run_id) / rel),
                kind=_kind_of(path),
                size_bytes=size,
                user_visible=not rel.name.startswith("."),
            ).model_dump())

        if docs:
            await self.repos.artifacts.col.delete_many({"run_id": run_id})
            await self.repos.artifacts.col.insert_many(docs)
            report.artifacts += len(docs)

    # ---------------------------------------------------------- projects

    async def _migrate_projects(self, root: Path, report: MigrationReport) -> None:
        projects_root = root / "workspace" / "projects"
        if not projects_root.exists():
            return

        for pdir in sorted(projects_root.iterdir()):
            if not pdir.is_dir():
                continue
            rec = _read_json(pdir / "project.json")
            if not isinstance(rec, dict):
                continue

            project_id = str(rec.get("project_id") or rec.get("id") or pdir.name)
            if await self.repos.projects.get(project_id) is None:
                await self.repos.projects.create(Project(
                    project_id=project_id,
                    name=str(rec.get("name") or project_id),
                    description=rec.get("description"),
                    owner_id=rec.get("owner_id") or rec.get("owner"),
                    archived=bool(rec.get("archived", False)),
                    tags=_as_list(rec.get("tags")),
                ))
                report.projects += 1

            rounds_dir = pdir / "rounds"
            if not rounds_dir.exists():
                continue
            for i, rpath in enumerate(sorted(rounds_dir.glob("*.json")), start=1):
                rrec = _read_json(rpath)
                if not isinstance(rrec, dict):
                    continue
                round_id = str(rrec.get("round_id") or rrec.get("id") or rpath.stem)
                if await self.repos.rounds.get(round_id) is not None:
                    continue
                await self.repos.rounds.create(Round(
                    round_id=round_id,
                    project_id=project_id,
                    name=rrec.get("name"),
                    index=int(rrec.get("index") or i),
                    linked_run_ids=_as_list(rrec.get("linked_run_ids")),
                    objective=rrec.get("objective"),
                    archived=bool(rrec.get("archived", False)),
                ))
                report.rounds += 1


# ---------------------------------------------------------------- helpers


def _basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).name


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _as_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for v in value:
        f = _as_float(v)
        if f is not None:
            out.append(f)
    return out


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _level_of(value: Any) -> str:
    text = str(value or "info").strip().lower()
    return text if text in {"debug", "info", "warning", "error"} else "info"


def _verdict_of(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"positive", "good", "up", "1", "true"}:
        return "positive"
    if text in {"negative", "bad", "down", "0", "false"}:
        return "negative"
    return "neutral"
