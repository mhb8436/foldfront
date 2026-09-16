from __future__ import annotations

import traceback
from typing import Any

import runpod

from embedder import embed_payload


def handler(event: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = event.get("input") if isinstance(event, dict) else {}
        if not isinstance(payload, dict):
            raise ValueError("RunPod event.input must be an object")
        return embed_payload(payload)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
