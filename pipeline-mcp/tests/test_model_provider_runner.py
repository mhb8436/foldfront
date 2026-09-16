from __future__ import annotations

import base64
import io
import os
from unittest.mock import patch

import numpy as np

from pipeline_mcp.app import build_runner
from pipeline_mcp.clients.esm_embedding import ESMEmbeddingRunPodClient
from pipeline_mcp.clients.esm_embedding import LocalHTTPESMEmbeddingClient
from pipeline_mcp.clients.local_http import LocalHTTPAlphaFold2Client
from pipeline_mcp.clients.local_http import LocalHTTPBioEmuClient
from pipeline_mcp.clients.local_http import LocalHTTPDiffDockClient
from pipeline_mcp.clients.local_http import LocalHTTPMMseqsClient
from pipeline_mcp.clients.local_http import LocalHTTPRFD3Client
from pipeline_mcp.clients.local_http import LocalHTTPRosettaRelaxClient
from pipeline_mcp.model_providers import ModelProviderStore


def _encoded_embedding_output(matrix: np.ndarray) -> dict:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, embeddings=np.asarray(matrix, dtype=np.float32))
    return {
        "ok": True,
        "embeddings_npz_b64": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


def test_build_runner_uses_http_model_provider_registry(tmp_path):
    store = ModelProviderStore(tmp_path)
    for model_key, port in {
        "mmseqs": 18106,
        "bioemu": 18103,
        "rfd3": 18104,
        "diffdock": 18105,
        "colabfold": 18161,
        "esm_embedding": 18170,
        "rosetta_relax": 18102,
    }.items():
        store.upsert(
            model_key,
            {
                "provider_type": "http_api",
                "base_url": f"http://gpu.example:{port}",
                "timeout_s": 222,
                "enabled": True,
            },
            actor="test",
        )

    env = {
        "RUNPOD_API_KEY": "runpod-key",
        "MMSEQS_ENDPOINT_ID": "mmseqs-runpod",
        "PROTEINMPNN_PROVIDER": "gpu_http",
        "PROTEINMPNN_GPU_URL": "http://gpu.example:18101",
        "PIPELINE_OUTPUT_ROOT": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=True):
        runner = build_runner()

    assert isinstance(runner.mmseqs, LocalHTTPMMseqsClient)
    assert isinstance(runner.bioemu, LocalHTTPBioEmuClient)
    assert isinstance(runner.rfd3, LocalHTTPRFD3Client)
    assert isinstance(runner.diffdock, LocalHTTPDiffDockClient)
    assert isinstance(runner.colabfold, LocalHTTPAlphaFold2Client)
    assert isinstance(runner.esm_embedding, LocalHTTPESMEmbeddingClient)
    assert isinstance(runner.rosetta_relax, LocalHTTPRosettaRelaxClient)
    assert runner.mmseqs.base_url == "http://gpu.example:18106"
    assert runner.colabfold.base_url == "http://gpu.example:18161"
    assert runner.esm_embedding.base_url == "http://gpu.example:18170"
    assert runner.rosetta_relax.base_url == "http://gpu.example:18102"


def test_build_runner_registry_runpod_provider_overrides_legacy_proteinmpnn_env(tmp_path):
    store = ModelProviderStore(tmp_path)
    store.upsert(
        "proteinmpnn",
        {
            "provider_type": "runpod",
            "endpoint_id": "registry-proteinmpnn",
            "token": "registry-runpod-key",
            "enabled": True,
        },
        actor="test",
    )

    env = {
        "RUNPOD_API_KEY": "runpod-key",
        "MMSEQS_ENDPOINT_ID": "mmseqs-runpod",
        "PROTEINMPNN_PROVIDER": "gpu_http",
        "PROTEINMPNN_GPU_URL": "http://legacy-gpu.example:18101",
        "PIPELINE_OUTPUT_ROOT": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=True):
        runner = build_runner()

    assert runner.proteinmpnn.endpoint_id == "registry-proteinmpnn"
    assert runner.proteinmpnn.gpu_url is None
    assert runner.proteinmpnn.runpod.api_key == "registry-runpod-key"


def test_build_runner_uses_user_model_provider_override(tmp_path):
    store = ModelProviderStore(tmp_path)
    store.upsert(
        "mmseqs",
        {
            "provider_type": "runpod",
            "endpoint_id": "global-mmseqs",
            "enabled": True,
        },
        actor="admin",
        scope="global",
    )
    store.upsert(
        "mmseqs",
        {
            "provider_type": "http_api",
            "base_url": "http://alice-gpu.example:18106",
            "enabled": True,
        },
        actor="alice",
        scope="user",
        user_id="alice",
    )

    env = {
        "RUNPOD_API_KEY": "runpod-key",
        "MMSEQS_ENDPOINT_ID": "env-mmseqs",
        "PROTEINMPNN_ENDPOINT_ID": "env-proteinmpnn",
        "PIPELINE_OUTPUT_ROOT": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=True):
        global_runner = build_runner()
        alice_runner = build_runner(provider_user="alice")

    assert not isinstance(global_runner.mmseqs, LocalHTTPMMseqsClient)
    assert isinstance(alice_runner.mmseqs, LocalHTTPMMseqsClient)
    assert alice_runner.mmseqs.base_url == "http://alice-gpu.example:18106"


def test_esm_embedding_runpod_client_chunks_large_requests():
    class FakeRunPod:
        def __init__(self) -> None:
            self.payload_sizes: list[int] = []

        def run_and_wait_with_job_id(self, endpoint_id, payload):
            rows = len(payload["sequences"])
            self.payload_sizes.append(rows)
            matrix = np.full((rows, 3), float(len(self.payload_sizes)), dtype=np.float32)
            return f"job-{len(self.payload_sizes)}", {
                "status": "COMPLETED",
                "output": _encoded_embedding_output(matrix),
            }

    fake_runpod = FakeRunPod()
    client = ESMEmbeddingRunPodClient(
        runpod=fake_runpod,
        endpoint_id="esm-endpoint",
        request_chunk_size=2,
    )

    embeddings = client.embed(["AAAA", "CCCC", "DDDD", "EEEE", "FFFF"])

    assert fake_runpod.payload_sizes == [2, 2, 1]
    assert embeddings.shape == (5, 3)
    assert embeddings[:, 0].tolist() == [1.0, 1.0, 2.0, 2.0, 3.0]
