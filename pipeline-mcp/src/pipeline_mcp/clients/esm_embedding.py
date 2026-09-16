from __future__ import annotations

import base64
from dataclasses import dataclass
import io
from typing import Any

import numpy as np
import requests

from .runpod import RunPodClient


DEFAULT_ESM_MODEL_NAME = "facebook/esm2_t6_8M_UR50D"
DEFAULT_REQUEST_CHUNK_SIZE = 2000


def _decode_embeddings(output: dict[str, Any]) -> np.ndarray:
    if not isinstance(output, dict):
        raise RuntimeError(f"ESM embedding output missing/invalid: {output!r}")
    if output.get("ok") is False or output.get("error"):
        raise RuntimeError(f"ESM embedding endpoint error: {output.get('error') or output}")
    encoded = str(output.get("embeddings_npz_b64") or "").strip()
    if not encoded:
        raise RuntimeError("ESM embedding output missing embeddings_npz_b64")
    data = base64.b64decode(encoded)
    with np.load(io.BytesIO(data)) as loaded:
        if "embeddings" not in loaded:
            raise RuntimeError("ESM embedding npz missing 'embeddings' array")
        matrix = np.asarray(loaded["embeddings"], dtype=np.float32)
    if matrix.ndim != 2:
        raise RuntimeError(f"ESM embedding matrix must be 2D, got shape={matrix.shape}")
    return matrix


def _sequence_payload(
    sequences: list[str],
    *,
    model_name: str = DEFAULT_ESM_MODEL_NAME,
    batch_size: int = 64,
    max_length: int = 1024,
) -> dict[str, Any]:
    return {
        "model_name": str(model_name or DEFAULT_ESM_MODEL_NAME),
        "batch_size": int(max(1, batch_size)),
        "max_length": int(max(1, max_length)),
        "sequences": [
            {"id": f"seq_{index + 1}", "sequence": str(sequence or "")}
            for index, sequence in enumerate(sequences)
        ],
    }


def _chunk_sequences(sequences: list[str], chunk_size: int) -> list[list[str]]:
    size = max(1, int(chunk_size or DEFAULT_REQUEST_CHUNK_SIZE))
    return [sequences[index : index + size] for index in range(0, len(sequences), size)]


@dataclass(frozen=True)
class ESMEmbeddingRunPodClient:
    runpod: RunPodClient
    endpoint_id: str
    model_name: str = DEFAULT_ESM_MODEL_NAME
    batch_size: int = 64
    max_length: int = 1024
    request_chunk_size: int = DEFAULT_REQUEST_CHUNK_SIZE

    def embed(self, sequences: list[str]) -> np.ndarray:
        chunks = _chunk_sequences(sequences, self.request_chunk_size)
        matrices: list[np.ndarray] = []
        for chunk in chunks:
            matrices.append(self._embed_chunk(chunk))
        if not matrices:
            return np.empty((0, 0), dtype=np.float32)
        return np.vstack(matrices)

    def _embed_chunk(self, sequences: list[str]) -> np.ndarray:
        payload = _sequence_payload(
            sequences,
            model_name=self.model_name,
            batch_size=self.batch_size,
            max_length=self.max_length,
        )
        _, result = self.runpod.run_and_wait_with_job_id(self.endpoint_id, payload)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(f"ESM embedding RunPod job not completed: {result}")
        output = result.get("output")
        return _decode_embeddings(output)


@dataclass(frozen=True)
class LocalHTTPESMEmbeddingClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0
    model_name: str = DEFAULT_ESM_MODEL_NAME
    batch_size: int = 64
    max_length: int = 1024
    request_chunk_size: int = DEFAULT_REQUEST_CHUNK_SIZE

    def embed(self, sequences: list[str]) -> np.ndarray:
        chunks = _chunk_sequences(sequences, self.request_chunk_size)
        matrices: list[np.ndarray] = []
        for chunk in chunks:
            matrices.append(self._embed_chunk(chunk))
        if not matrices:
            return np.empty((0, 0), dtype=np.float32)
        return np.vstack(matrices)

    def _embed_chunk(self, sequences: list[str]) -> np.ndarray:
        payload = _sequence_payload(
            sequences,
            model_name=self.model_name,
            batch_size=self.batch_size,
            max_length=self.max_length,
        )
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = requests.post(
            f"{self.base_url.rstrip('/')}/embed",
            json=payload,
            headers=headers,
            timeout=float(self.timeout_s),
        )
        response.raise_for_status()
        output = response.json()
        return _decode_embeddings(output)
