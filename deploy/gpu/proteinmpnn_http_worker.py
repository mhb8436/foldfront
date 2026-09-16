#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import json
import os
from pathlib import Path
import traceback
from typing import Any


_HANDLER = None


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _load_handler():
    global _HANDLER
    if _HANDLER is not None:
        return _HANDLER
    handler_path = Path(os.getenv("PROTEINMPNN_HANDLER_PATH", "/workspace/handler.py"))
    if not handler_path.exists():
        raise RuntimeError(f"handler.py not found: {handler_path}")
    spec = importlib.util.spec_from_file_location("proteinmpnn_runpod_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load handler module from {handler_path}")
    module = importlib.util.module_from_spec(spec)
    serverless = None
    original_start = None
    try:
        serverless = importlib.import_module("runpod.serverless")
        original_start = getattr(serverless, "start", None)
        # RunPod serverless handlers usually call start() at import time. For this
        # HTTP worker we only need the handler callable, not the serverless loop.
        serverless.start = lambda *_args, **_kwargs: None
    except Exception:
        serverless = None
    try:
        spec.loader.exec_module(module)
    finally:
        if serverless is not None and original_start is not None:
            serverless.start = original_start
    fn = getattr(module, "handler", None)
    if not callable(fn):
        raise RuntimeError(f"handler function not found in {handler_path}")
    _HANDLER = fn
    return _HANDLER


def _required_token() -> str | None:
    return os.getenv("PROTEINMPNN_WORKER_TOKEN", "").strip() or None


class ProteinMPNNWorkerHandler(BaseHTTPRequestHandler):
    server_version = "ProteinMPNNHTTPWorker/1.0"

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/healthz":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        handler_path = Path(os.getenv("PROTEINMPNN_HANDLER_PATH", "/workspace/handler.py"))
        _json_response(
            self,
            HTTPStatus.OK,
            {
                "ok": True,
                "handler_path": str(handler_path),
                "handler_exists": handler_path.exists(),
            },
        )

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/run":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        token = _required_token()
        if token:
            expected = f"Bearer {token}"
            if self.headers.get("Authorization") != expected:
                _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            request = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(request, dict):
                raise ValueError("request JSON must be an object")
            payload = request.get("input", request)
            result = _load_handler()({"input": payload})
            if isinstance(result, dict) and ("output" in result or "error" in result or "status" in result):
                _json_response(self, HTTPStatus.OK, result)
                return
            _json_response(self, HTTPStatus.OK, {"status": "COMPLETED", "output": result})
        except Exception as exc:  # pragma: no cover - depends on deployed handler behavior.
            _json_response(
                self,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "status": "FAILED",
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("PROTEINMPNN_WORKER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PROTEINMPNN_WORKER_PORT", "18101")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ProteinMPNNWorkerHandler)
    print(f"listening: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
