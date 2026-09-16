from __future__ import annotations

import json
import sys
from typing import Any

from .app import build_runner
from .tools import ToolDispatcher


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


def main() -> None:
    dispatcher = ToolDispatcher(build_runner())

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
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": dispatcher.list_tools()}
                _write_message(resp)
                continue
            if method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Invalid tools/call params")
                out = dispatcher.call_tool(name, arguments)
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": _result_text(out)}
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

