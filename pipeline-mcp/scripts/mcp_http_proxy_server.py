from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


_AUTH_TOKEN: str | None = None


def _get_auth_token(base_url: str, *, timeout_s: float) -> str | None:
    global _AUTH_TOKEN
    if _AUTH_TOKEN:
        return _AUTH_TOKEN

    token = os.environ.get("PIPELINE_AUTH_TOKEN", "").strip()
    if token:
        _AUTH_TOKEN = token
        return token

    username = os.environ.get("PIPELINE_AUTH_USERNAME", "").strip()
    password = os.environ.get("PIPELINE_AUTH_PASSWORD", "").strip()
    if not username or not password:
        return None

    payload = {"username": username, "password": password}
    r = requests.post(f"{base_url}/auth/login", json=payload, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError("Auth login failed")
    token = str(data.get("token") or "")
    if not token:
        raise RuntimeError("Auth token missing")
    _AUTH_TOKEN = token
    return token


def _auth_headers(base_url: str, *, timeout_s: float) -> dict[str, str]:
    token = _get_auth_token(base_url, timeout_s=timeout_s)
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        s = line.decode("utf-8", errors="replace").strip()
        if not s:
            break
        if ":" in s:
            k, v = s.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8", errors="replace"))


def _write_message(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _result_text(obj: object) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}]}


def _error(err: Exception) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"ERROR: {err}"}], "isError": True}


def _post_json(url: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    base_url = url.split("/tools/", 1)[0]
    headers = _auth_headers(base_url, timeout_s=timeout_s)
    r = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON response: {data!r}")
    return data


def _remote_status(base_url: str, *, run_id: str, timeout_s: float) -> dict[str, Any] | None:
    data = _post_json(
        f"{base_url}/tools/call",
        {"name": "pipeline.status", "arguments": {"run_id": run_id}},
        timeout_s=timeout_s,
    )
    if not data.get("ok", False):
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    if not result.get("found"):
        return None
    status = result.get("status")
    if not isinstance(status, dict):
        return None
    return status


def _maybe_block_duplicate_run(
    base_url: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any] | None:
    if tool_name not in {"pipeline.run", "pipeline.run_from_prompt"}:
        return None

    run_id_raw = arguments.get("run_id")
    if run_id_raw is None:
        return None
    run_id = str(run_id_raw).strip()
    if not run_id:
        return None

    status = _remote_status(base_url, run_id=run_id, timeout_s=min(timeout_s, 30.0))
    if status is None:
        return None

    state = str(status.get("state") or "").strip().lower()
    if state != "running":
        return None

    return {
        "run_id": run_id,
        "already_running": True,
        "status": status,
        "message": "Run is already running. Poll with pipeline.status; avoid re-calling pipeline.run to prevent duplicate jobs.",
    }


def main(argv: list[str] | None = None) -> None:
    pipeline_mcp_dir = Path(__file__).resolve().parents[1]
    _load_dotenv(pipeline_mcp_dir / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PIPELINE_MCP_HTTP_URL")
        or os.environ.get("PIPELINE_MCP_BASE_URL")
        or os.environ.get("PIPELINE_HTTP_URL")
        or "http://127.0.0.1:8000",
        help="Base URL for pipeline-mcp HTTP server (expects /tools/list and /tools/call).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=float(os.environ.get("PIPELINE_MCP_HTTP_TIMEOUT_S") or "3600"),
        help="HTTP timeout (seconds) for remote tool calls.",
    )
    args = parser.parse_args(argv)
    base_url = str(args.base_url).rstrip("/")
    timeout_s = float(args.timeout_s)

    while True:
        msg = _read_message()
        if msg is None:
            break
        method = msg.get("method")
        msg_id = msg.get("id")

        try:
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
                _write_message(resp)
                continue

            if method == "tools/list":
                data = _post_json(f"{base_url}/tools/list", {}, timeout_s=timeout_s)
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": data}
                _write_message(resp)
                continue

            if method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Invalid tools/call params")

                blocked = _maybe_block_duplicate_run(
                    base_url,
                    tool_name=name,
                    arguments=arguments,
                    timeout_s=timeout_s,
                )
                if blocked is not None:
                    resp = {"jsonrpc": "2.0", "id": msg_id, "result": _result_text(blocked)}
                    _write_message(resp)
                    continue

                data = _post_json(
                    f"{base_url}/tools/call",
                    {"name": name, "arguments": arguments},
                    timeout_s=timeout_s,
                )
                if not data.get("ok", False):
                    raise RuntimeError(data.get("error") or "Remote /tools/call failed")
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": _result_text(data.get("result"))}
                _write_message(resp)
                continue

            if msg_id is not None:
                resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
                _write_message(resp)
        except Exception as exc:
            if msg_id is None:
                continue
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": _error(exc)}
            _write_message(resp)


if __name__ == "__main__":
    main()
