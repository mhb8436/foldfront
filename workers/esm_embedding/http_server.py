from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from embedder import embed_payload


app = FastAPI(title="RAPID ESM Embedding Worker")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return embed_payload({"health": True})


@app.post("/embed")
def embed(payload: dict[str, Any]) -> dict[str, Any]:
    result = embed_payload(payload)
    if not result.get("ok", False):
        raise HTTPException(status_code=500, detail=result)
    return result
