import base64
import os
import unittest
from unittest.mock import patch

from pipeline_mcp.clients.proteinmpnn import ProteinMPNNClient
from pipeline_mcp.config import load_config


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class ProteinMPNNGpuHttpClientTest(unittest.TestCase):
    def test_posts_runpod_payload_to_gpu_worker_and_parses_output(self):
        calls = []

        def fake_post(url, *, headers, json, timeout):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": timeout,
                }
            )
            return _FakeResponse(
                {
                    "status": "COMPLETED",
                    "output": {
                        "native": {"name": "native", "header": "native", "sequence": "AAA"},
                        "samples": [{"name": "s1", "header": "sample 1", "sequence": "AFA"}],
                    },
                }
            )

        client = ProteinMPNNClient(
            runpod=None,
            endpoint_id=None,
            gpu_url="http://gpu.internal:18101/",
            gpu_token="worker-secret",
            gpu_timeout_s=123.0,
        )

        # ProteinMPNNClient delegates the actual HTTP call to LocalHttpRunClient
        # (see local_http.py) -- that's where requests.post is actually called.
        with patch("pipeline_mcp.clients.local_http.requests.post", fake_post):
            native, samples, raw = client.design(
                pdb_text="ATOM\nEND\n",
                pdb_name="target",
                num_seq_per_target=2,
                fixed_positions={"A": [1, 2]},
            )

        self.assertEqual(native.sequence, "AAA")
        self.assertEqual(samples[0].sequence, "AFA")
        self.assertEqual(raw["native"]["sequence"], "AAA")
        self.assertEqual(calls[0]["url"], "http://gpu.internal:18101/run")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer worker-secret")
        # The submit call uses the caller's full configured timeout, since a
        # worker not yet migrated to the async pattern won't answer /run
        # until the job is actually done.
        self.assertEqual(calls[0]["timeout"], 123.0)
        sent_input = calls[0]["json"]["input"]
        self.assertEqual(sent_input["pdb_name"], "target")
        self.assertEqual(sent_input["fixed_positions"], {"A": [1, 2]})
        self.assertEqual(base64.b64decode(sent_input["pdb_base64"]).decode("utf-8"), "ATOM\nEND\n")

    def test_polls_status_when_the_worker_answers_pending(self):
        # Regression test: the ProteinMPNN worker on bop was migrated to the
        # async submit+poll pattern (answers /run with PENDING immediately,
        # finishes in the background). ProteinMPNNClient used to make its own
        # raw requests.post call here instead of going through
        # LocalHttpRunClient, so it treated the immediate PENDING response as
        # the final (invalid) result and crashed every real job with
        # "ProteinMPNN GPU output missing/invalid: {'status': 'PENDING'}".
        get_calls = []

        def fake_post(url, *, headers, json, timeout):
            return _FakeResponse({"id": "job-1", "status": "PENDING"})

        def fake_get(url, *, headers, params, timeout):
            get_calls.append(params)
            if len(get_calls) < 2:
                return _FakeResponse({"id": "job-1", "status": "RUNNING"})
            return _FakeResponse(
                {
                    "id": "job-1",
                    "status": "COMPLETED",
                    "output": {
                        "native": {"name": "native", "header": "native", "sequence": "AAA"},
                        "samples": [{"name": "s1", "header": "sample 1", "sequence": "AFA"}],
                    },
                }
            )

        client = ProteinMPNNClient(
            runpod=None,
            endpoint_id=None,
            gpu_url="http://gpu.internal:18101/",
            gpu_timeout_s=60.0,
        )

        with patch("pipeline_mcp.clients.local_http.requests.post", fake_post), \
             patch("pipeline_mcp.clients.local_http.requests.get", fake_get), \
             patch("time.sleep", lambda _s: None):
            native, samples, raw = client.design(
                pdb_text="ATOM\nEND\n",
                pdb_name="target",
                num_seq_per_target=2,
                fixed_positions={"A": [1, 2]},
            )

        self.assertEqual(native.sequence, "AAA")
        self.assertEqual(samples[0].sequence, "AFA")
        self.assertEqual(len(get_calls), 2)
        self.assertTrue(all(call["id"] == "job-1" for call in get_calls))


class ProteinMPNNGpuHttpConfigTest(unittest.TestCase):
    def test_gpu_http_provider_does_not_require_runpod_proteinmpnn_endpoint(self):
        env = {
            "RUNPOD_API_KEY": "runpod-key",
            "MMSEQS_ENDPOINT_ID": "mmseqs-endpoint",
            "PROTEINMPNN_PROVIDER": "gpu_http",
            "PROTEINMPNN_GPU_URL": "http://proteinmpnn-gpu.example.org:18101",
            "PROTEINMPNN_GPU_TOKEN": "worker-secret",
            "PROTEINMPNN_GPU_TIMEOUT_S": "456",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()

        self.assertEqual(cfg.proteinmpnn.provider, "gpu_http")
        self.assertIsNone(cfg.runpod.proteinmpnn_endpoint_id)
        self.assertEqual(cfg.proteinmpnn.gpu_url, "http://proteinmpnn-gpu.example.org:18101")
        self.assertEqual(cfg.proteinmpnn.gpu_token, "worker-secret")
        self.assertEqual(cfg.proteinmpnn.gpu_timeout_s, 456.0)


if __name__ == "__main__":
    unittest.main()
