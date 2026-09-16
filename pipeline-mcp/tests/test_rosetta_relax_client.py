import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_mcp.clients.rosetta_relax import RosettaRelaxClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class TestRosettaRelaxRunPodClient(unittest.TestCase):
    def test_runpod_failed_status_raises_with_output_detail(self) -> None:
        responses = iter(
            [
                _FakeResponse({"id": "job-1"}),
                _FakeResponse(
                    {
                        "id": "job-1",
                        "status": "FAILED",
                        "output": {"error": "Rosetta database directory not found"},
                    }
                ),
            ]
        )

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            return next(responses)

        client = RosettaRelaxClient(timeout_s=1.0)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch(
            "time.time", side_effect=[0.0, 0.0, 2.0]
        ), patch("time.sleep", return_value=None):
            with self.assertRaisesRegex(
                RuntimeError, "FAILED.*Rosetta database directory not found"
            ):
                client._runpod_sync_call("endpoint-1", "api-key", {"input": {}})

    def test_runpod_payload_includes_client_timeout(self) -> None:
        captured: dict = {}

        def fake_sync(_self, endpoint_id, api_key, payload):  # type: ignore[no-untyped-def]
            captured["endpoint_id"] = endpoint_id
            captured["api_key"] = api_key
            captured["payload"] = payload
            return {"relaxed_pdb_content": "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00  0.00           C\n", "score_per_res": 1.5}

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"RUNPOD_RELAX_ENDPOINT_ID": "endpoint-1", "RUNPOD_API_KEY": "api-key"},
        ):
            tmp = Path(tmpdir)
            input_pdb = tmp / "input.pdb"
            input_pdb.write_text(
                "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00  0.00           C\n",
                encoding="utf-8",
            )
            client = RosettaRelaxClient(timeout_s=3600.0)
            with patch.object(RosettaRelaxClient, "_runpod_sync_call", fake_sync):
                client.run(input_pdb, tmp / "out")

        self.assertEqual(captured["endpoint_id"], "endpoint-1")
        self.assertEqual(captured["api_key"], "api-key")
        self.assertEqual(captured["payload"]["input"]["timeout_s"], 3600)


if __name__ == "__main__":
    unittest.main()
