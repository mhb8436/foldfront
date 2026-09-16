from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

from .storage import ensure_dir
from .storage import load_status
from .storage import write_json


_CATH_SUBSETS: tuple[str, ...] = ("train", "val", "test")
_JOB_KIND_BATCH = "cath_batch"
_JOB_KIND_TRAIN = "cath_train"
_ACTIVE_JOB_STATES = {"queued", "running", "stopping"}
_STOPPED_JOB_STATES = {"cancelled", "canceled", "stopped"}
_TERMINAL_JOB_STATES = _STOPPED_JOB_STATES | {"completed", "failed", "error"}
_FAILED_STATUS_STATES = {"failed", "error"}
_STOPPED_STATUS_STATES = {"cancelled", "canceled", "stopped"}


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def workspace_root_from_output_root(output_root: str) -> Path:
    return Path(output_root).resolve().parent


def subset_target_dir(output_root: str, subset: str) -> Path:
    return workspace_root_from_output_root(output_root) / f"cath_{subset}"


def batch_log_paths(output_root: str, subset: str) -> dict[str, Path]:
    root = workspace_root_from_output_root(output_root)
    return {
        "success": root / f"batch_success_{subset}.csv",
        "failed": root / f"batch_failed_{subset}.csv",
    }


def managed_jobs_root(output_root: str) -> Path:
    return ensure_dir(workspace_root_from_output_root(output_root) / "_ops" / "jobs")


def _job_root(output_root: str, job_id: str) -> Path:
    return managed_jobs_root(output_root) / str(job_id).strip()


def _job_meta_path(output_root: str, job_id: str) -> Path:
    return _job_root(output_root, job_id) / "job.json"


def _job_log_path(output_root: str, job_id: str) -> Path:
    return _job_root(output_root, job_id) / "job.log"


def _safe_subset(subset: object) -> str:
    text = str(subset or "").strip().lower()
    if text not in _CATH_SUBSETS:
        raise ValueError(f"subset must be one of {_CATH_SUBSETS}")
    return text


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader if isinstance(row, dict)]
    except Exception:
        return []


def _job_state(job: dict[str, Any]) -> str:
    return str(job.get("state") or "").strip().lower()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _job_process_alive(job: dict[str, Any]) -> bool:
    for key in ("child_pid", "helper_pid", "launcher_pid"):
        try:
            pid = int(job.get(key) or 0)
        except Exception:
            pid = 0
        if _pid_alive(pid):
            return True
    return False


def _job_subset(job: dict[str, Any]) -> str:
    metadata = job.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("subset") or "").strip().lower()


def _managed_batch_jobs_for_subset(output_root: str, subset: str) -> list[dict[str, Any]]:
    root = managed_jobs_root(output_root)
    jobs: list[dict[str, Any]] = []
    for job_dir in root.iterdir():
        if not job_dir.is_dir():
            continue
        meta = _job_payload(output_root, job_dir.name)
        if not isinstance(meta, dict):
            continue
        if str(meta.get("kind") or "").strip() != _JOB_KIND_BATCH:
            continue
        if _job_subset(meta) != subset:
            continue
        jobs.append(meta)
    jobs.sort(
        key=lambda item: (
            str(item.get("finished_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("job_id") or ""),
        ),
        reverse=True,
    )
    return jobs


def _latest_terminal_job(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for job in jobs:
        if _job_state(job) in _TERMINAL_JOB_STATES:
            return job
    return None


def _active_managed_batch_job(
    output_root: str, subset: str
) -> dict[str, Any] | None:
    for job in _managed_batch_jobs_for_subset(output_root, subset):
        if _job_state(job) in _ACTIVE_JOB_STATES and _job_process_alive(job):
            return job
    return None


def _stopped_detail(existing: str, job: dict[str, Any] | None) -> str:
    pieces = [existing] if existing else []
    if isinstance(job, dict):
        state = _job_state(job) or "finished"
        job_id = str(job.get("job_id") or "").strip()
        when = str(
            job.get("finished_at")
            or job.get("stop_requested_at")
            or job.get("created_at")
            or ""
        ).strip()
        summary = f"managed batch {state}"
        if job_id:
            summary = f"{summary}: {job_id}"
        if when:
            summary = f"{summary} at {when}"
        pieces.append(summary)
    if not pieces:
        return "managed batch stopped"
    return "; ".join(pieces)


def _cath_item_sort_priority(item: dict[str, Any]) -> int:
    state = str(item.get("state") or "").strip().lower()
    if state == "running":
        return 0
    if state in _FAILED_STATUS_STATES:
        return 1
    if state in _STOPPED_STATUS_STATES:
        return 2
    return 3


def _success_ids(output_root: str, subset: str) -> set[str]:
    rows = _read_csv_rows(batch_log_paths(output_root, subset)["success"])
    return {
        str(row.get("run_id") or "").strip()
        for row in rows
        if str(row.get("run_id") or "").strip()
    }


def _failed_info(output_root: str, subset: str) -> dict[str, dict[str, str]]:
    rows = _read_csv_rows(batch_log_paths(output_root, subset)["failed"])
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        out[run_id] = {
            "timestamp": str(row.get("timestamp") or "").strip(),
            "error": str(row.get("error") or "").strip(),
        }
    return out


def expected_run_ids(output_root: str, subset: str) -> list[str]:
    target_dir = subset_target_dir(output_root, subset)
    if not target_dir.exists():
        return []
    return sorted(f"cath_{subset}_{path.stem}" for path in target_dir.glob("*.pdb"))


def summarize_subset(
    output_root: str, subset: str, *, item_limit: int = 200
) -> dict[str, Any]:
    subset = _safe_subset(subset)
    output_root_path = Path(output_root).resolve()
    success_ids = _success_ids(output_root, subset)
    failed = _failed_info(output_root, subset)
    run_ids = expected_run_ids(output_root, subset)
    batch_jobs = _managed_batch_jobs_for_subset(output_root, subset)
    has_active_batch_job = any(_job_state(job) in _ACTIVE_JOB_STATES for job in batch_jobs)
    latest_terminal_job = _latest_terminal_job(batch_jobs)
    stale_running_is_stopped = bool(latest_terminal_job and not has_active_batch_job)
    items: list[dict[str, Any]] = []
    counts = {
        "total": len(run_ids),
        "completed": 0,
        "failed": 0,
        "running": 0,
        "stopped": 0,
        "waiting": 0,
        "other": 0,
    }

    for run_id in run_ids:
        run_root = output_root_path / run_id
        target_id = run_id.removeprefix(f"cath_{subset}_")
        status = load_status(output_root, run_id)
        report_exists = (run_root / "report.md").exists()
        status_stage = "-"
        status_state = "waiting"
        detail = ""
        updated_at = ""
        source = "expected"

        if run_id in success_ids or report_exists:
            status_stage = "done"
            status_state = "completed"
            source = "success_log" if run_id in success_ids else "local_report"
            counts["completed"] += 1
        elif run_id in failed:
            status_stage = "failed"
            status_state = "failed"
            detail = str(failed[run_id].get("error") or "")
            updated_at = str(failed[run_id].get("timestamp") or "")
            source = "failed_log"
            counts["failed"] += 1
        elif isinstance(status, dict):
            status_stage = str(status.get("stage") or "-")
            status_state = (
                str(status.get("state") or "unknown").strip().lower() or "unknown"
            )
            detail = str(status.get("detail") or "")
            updated_at = str(status.get("updated_at") or "")
            source = "status_json"
            if status_state == "running":
                if stale_running_is_stopped:
                    status_state = "stopped"
                    detail = _stopped_detail(detail, latest_terminal_job)
                    source = "stale_status_json"
                    counts["stopped"] += 1
                else:
                    counts["running"] += 1
            elif status_state == "completed":
                counts["completed"] += 1
            elif status_state in _FAILED_STATUS_STATES:
                counts["failed"] += 1
            elif status_state in _STOPPED_STATUS_STATES:
                counts["stopped"] += 1
            else:
                counts["other"] += 1
        elif run_root.exists():
            status_stage = "init"
            status_state = "starting"
            source = "output_dir"
            counts["other"] += 1
        else:
            counts["waiting"] += 1

        items.append(
            {
                "run_id": run_id,
                "target_id": target_id,
                "subset": subset,
                "stage": status_stage,
                "state": status_state,
                "detail": detail,
                "updated_at": updated_at,
                "source": source,
            }
        )

    items.sort(
        key=lambda item: (
            _cath_item_sort_priority(item),
            str(item.get("updated_at") or ""),
            str(item.get("run_id") or ""),
        )
    )
    if item_limit > 0:
        items = items[:item_limit]

    return {
        "subset": subset,
        "target_dir": str(subset_target_dir(output_root, subset)),
        "logs": {
            key: str(path) for key, path in batch_log_paths(output_root, subset).items()
        },
        "counts": counts,
        "items": items,
    }


def summarize_all_subsets(output_root: str, *, item_limit: int = 200) -> dict[str, Any]:
    subsets = {
        subset: summarize_subset(output_root, subset, item_limit=item_limit)
        for subset in _CATH_SUBSETS
    }
    totals = {
        "total": sum(int(entry["counts"]["total"]) for entry in subsets.values()),
        "completed": sum(
            int(entry["counts"]["completed"]) for entry in subsets.values()
        ),
        "failed": sum(int(entry["counts"]["failed"]) for entry in subsets.values()),
        "running": sum(int(entry["counts"]["running"]) for entry in subsets.values()),
        "stopped": sum(int(entry["counts"]["stopped"]) for entry in subsets.values()),
        "waiting": sum(int(entry["counts"]["waiting"]) for entry in subsets.values()),
        "other": sum(int(entry["counts"]["other"]) for entry in subsets.values()),
    }
    return {"subsets": subsets, "totals": totals}


def _job_payload(output_root: str, job_id: str) -> dict[str, Any] | None:
    path = _job_meta_path(output_root, job_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def list_managed_jobs(
    output_root: str,
    *,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    root = managed_jobs_root(output_root)
    jobs: list[dict[str, Any]] = []
    for job_dir in root.iterdir():
        if not job_dir.is_dir():
            continue
        meta = _job_payload(output_root, job_dir.name)
        if not isinstance(meta, dict):
            continue
        if not str(meta.get("job_id") or "").strip():
            meta = {**meta, "job_id": job_dir.name}
        if kind and str(meta.get("kind") or "").strip() != kind:
            continue
        jobs.append(meta)
    jobs.sort(
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("job_id") or ""),
        ),
        reverse=True,
    )
    if limit > 0:
        jobs = jobs[:limit]
    return jobs


def read_managed_job(output_root: str, job_id: str) -> dict[str, Any]:
    meta = _job_payload(output_root, job_id)
    if not isinstance(meta, dict):
        raise ValueError("job_id not found")
    return meta


def read_managed_job_log(
    output_root: str,
    job_id: str,
    *,
    max_bytes: int = 120_000,
) -> dict[str, Any]:
    meta = read_managed_job(output_root, job_id)
    log_path = _job_log_path(output_root, job_id)
    if not log_path.exists():
        return {"job": meta, "text": "", "path": str(log_path), "bytes": 0}
    data = log_path.read_bytes()
    if max_bytes > 0 and len(data) > max_bytes:
        data = data[-max_bytes:]
    return {
        "job": meta,
        "text": data.decode("utf-8", errors="replace"),
        "path": str(log_path),
        "bytes": log_path.stat().st_size,
    }


def _update_job_meta(
    output_root: str, job_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    current = read_managed_job(output_root, job_id)
    merged = {**current, **updates}
    write_json(_job_meta_path(output_root, job_id), merged)
    return merged


def stop_managed_job(output_root: str, job_id: str) -> dict[str, Any]:
    current = read_managed_job(output_root, job_id)
    helper_pid = int(current.get("helper_pid") or current.get("launcher_pid") or 0)
    child_pid = int(current.get("child_pid") or 0)
    stopped = False
    errors: list[str] = []

    for pid in (helper_pid, child_pid):
        if pid <= 0:
            continue
        try:
            os.kill(pid, 15)
            stopped = True
            break
        except ProcessLookupError:
            continue
        except Exception as exc:
            errors.append(str(exc))

    meta = _update_job_meta(
        output_root,
        job_id,
        {
            "stop_requested_at": _iso_utc_now(),
            "stop_request_error": "; ".join(errors) if errors else None,
        },
    )
    return {"stopped": stopped, "job": meta}


def delete_managed_job(output_root: str, job_id: str) -> dict[str, Any]:
    root = managed_jobs_root(output_root).resolve()
    target = (root / str(job_id or "").strip()).resolve()
    if root not in target.parents:
        raise ValueError("invalid job_id")
    meta = _job_payload(output_root, target.name)
    if not target.exists():
        return {"job_id": target.name, "found": False, "deleted": False}
    shutil.rmtree(target)
    return {
        "job_id": target.name,
        "found": True,
        "deleted": True,
        "job": meta or {"job_id": target.name},
    }


def _launch_helper(
    output_root: str,
    *,
    kind: str,
    label: str,
    command: list[str],
    metadata: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    workspace_root = workspace_root_from_output_root(output_root)
    job_id = (
        f"{kind}_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}_{uuid.uuid4().hex[:8]}"
    )
    job_root = ensure_dir(_job_root(output_root, job_id))
    meta_path = job_root / "job.json"
    log_path = job_root / "job.log"
    helper_script = workspace_root / "scripts" / "run_managed_job.py"
    if not helper_script.exists():
        raise ValueError(f"managed job helper is missing: {helper_script}")
    requested_cwd = str(Path(cwd or workspace_root).resolve())
    initial = {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "state": "queued",
        "created_at": _iso_utc_now(),
        "cwd": requested_cwd,
        "command": list(command),
        "metadata": metadata or {},
        "log_path": str(log_path),
    }
    write_json(meta_path, initial)

    helper_cmd = [
        sys.executable,
        str(helper_script),
        "--job-json",
        str(meta_path),
        "--log-file",
        str(log_path),
        "--cwd",
        requested_cwd,
        "--",
        *command,
    ]
    proc = subprocess.Popen(
        helper_cmd,
        cwd=str(workspace_root),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_json(meta_path, {**initial, "launcher_pid": proc.pid})
    return read_managed_job(output_root, job_id)


def launch_cath_batch_job(
    output_root: str,
    *,
    subset: str,
    keep_local: bool = False,
    stop_on_error: bool = False,
    max_workers: int | None = None,
) -> dict[str, Any]:
    subset = _safe_subset(subset)
    active_job = _active_managed_batch_job(output_root, subset)
    if active_job is not None:
        return {
            **active_job,
            "already_running": True,
            "message": f"CATH batch ({subset}) is already running",
        }

    workspace_root = workspace_root_from_output_root(output_root)
    command = [
        sys.executable,
        str(workspace_root / "scripts" / "02_run_cath_batch.py"),
        "--subset",
        subset,
    ]
    if keep_local:
        command.append("--keep-local")
    if stop_on_error:
        command.append("--stop-on-error")
    if isinstance(max_workers, int) and max_workers > 0:
        command.extend(["--max-workers", str(int(max_workers))])
    return _launch_helper(
        output_root,
        kind=_JOB_KIND_BATCH,
        label=f"CATH batch ({subset})",
        command=command,
        metadata={
            "subset": subset,
            "keep_local": bool(keep_local),
            "stop_on_error": bool(stop_on_error),
            "max_workers": max_workers,
        },
    )


def launch_cath_training_job(
    output_root: str,
    *,
    subsets: list[str],
) -> dict[str, Any]:
    cleaned = [_safe_subset(subset) for subset in subsets]
    if not cleaned:
        raise ValueError("at least one subset is required")
    workspace_root = workspace_root_from_output_root(output_root)
    command = [
        sys.executable,
        str(workspace_root / "scripts" / "train_cath_surrogate.py"),
        "--subsets",
        ",".join(cleaned),
    ]
    return _launch_helper(
        output_root,
        kind=_JOB_KIND_TRAIN,
        label=f"CATH surrogate training ({','.join(cleaned)})",
        command=command,
        metadata={"subsets": cleaned},
    )


def job_kind_batch() -> str:
    return _JOB_KIND_BATCH


def job_kind_train() -> str:
    return _JOB_KIND_TRAIN
