from __future__ import annotations

import os
import time


def log(message: str) -> None:
    if os.environ.get("PIPELINE_MCP_QUIET", "").strip().lower() in {"1", "true", "yes"}:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{ts}] {message}", flush=True)

