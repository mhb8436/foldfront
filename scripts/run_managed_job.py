#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Managed long-running job wrapper")
    parser.add_argument("--job-json", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    args.command = command
    return args


def main() -> int:
    args = _parse_args()
    if not args.command:
        raise SystemExit("command is required")

    job_json = Path(args.job_json).resolve()
    log_file = Path(args.log_file).resolve()
    cwd = Path(args.cwd).resolve()
    meta = _read_json(job_json)
    meta.update(
        {
            "helper_pid": os.getpid(),
            "started_at": _iso_utc_now(),
            "state": "running",
            "cwd": str(cwd),
            "command": list(args.command),
        }
    )
    _write_json(job_json, meta)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    stop_requested = {"value": False}
    child_holder: dict[str, subprocess.Popen[str]] = {}

    def _request_stop(signum: int, _frame) -> None:
        stop_requested["value"] = True
        current = _read_json(job_json)
        current["stop_requested_at"] = _iso_utc_now()
        current["state"] = "stopping"
        _write_json(job_json, current)
        child = child_holder.get("proc")
        if child is None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except Exception:
            try:
                child.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    with log_file.open("a", encoding="utf-8", newline="\n") as log_handle:
        log_handle.write(
            f"[{_iso_utc_now()}] starting: {' '.join(args.command)}\n"
        )
        log_handle.flush()
        proc = subprocess.Popen(
            args.command,
            cwd=str(cwd),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        child_holder["proc"] = proc
        current = _read_json(job_json)
        current["child_pid"] = proc.pid
        current["state"] = "running"
        _write_json(job_json, current)
        return_code = proc.wait()
        finished = _read_json(job_json)
        finished["finished_at"] = _iso_utc_now()
        finished["return_code"] = int(return_code)
        if stop_requested["value"]:
            finished["state"] = "cancelled"
        else:
            finished["state"] = "completed" if return_code == 0 else "failed"
        _write_json(job_json, finished)
        log_handle.write(
            f"[{_iso_utc_now()}] finished with code {return_code}\n"
        )
        log_handle.flush()
        return int(return_code)


if __name__ == "__main__":
    raise SystemExit(main())
