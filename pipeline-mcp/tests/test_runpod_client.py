import base64
import io
import json
import tarfile
import unittest

import requests

from pipeline_mcp.clients.alphafold2_runpod import AlphaFold2RunPodClient
from pipeline_mcp.clients.runpod import RunPodClient
from pipeline_mcp.models import SequenceRecord


class _FakeResponse:
    def __init__(self, status_code: int, payload: bytes = b"{}") -> None:
        self.status_code = status_code
        self.content = payload

    def raise_for_status(self) -> None:
        raise requests.HTTPError(response=self)


def _af2_archive_base64(*, plddt: float = 91.5, pdb_text: str = "MODEL 1\nENDMDL\n") -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        ranking_payload = json.dumps({"order": ["model_1"], "plddts": {"model_1": plddt}}).encode("utf-8")
        ranking_info = tarfile.TarInfo("ranking_debug.json")
        ranking_info.size = len(ranking_payload)
        tar.addfile(ranking_info, io.BytesIO(ranking_payload))

        pdb_payload = pdb_text.encode("utf-8")
        pdb_info = tarfile.TarInfo("ranked_0.pdb")
        pdb_info.size = len(pdb_payload)
        tar.addfile(pdb_info, io.BytesIO(pdb_payload))
    return base64.b64encode(raw.getvalue()).decode("ascii")


def _missing_pdb_job() -> dict[str, object]:
    return {
        "status": "COMPLETED",
        "output": {
            "status": "error",
            "details": (
                "RuntimeError: colabfold_batch completed but no PDB outputs were found. "
                "Check your localcolabfold installation, databases, and colabfold_args."
            ),
        },
    }


def _successful_af2_job() -> dict[str, object]:
    return {
        "status": "COMPLETED",
        "output": {
            "archives": [
                {
                    "name": "alphafold_results.tar.gz",
                    "base64": _af2_archive_base64(),
                }
            ]
        },
    }


class TestRunPodClient(unittest.TestCase):
    def test_raise_for_status_rewrites_unauthorized_message(self) -> None:
        client = RunPodClient(api_key="bad-key")
        with self.assertRaisesRegex(RuntimeError, "RUNPOD_API_KEY was rejected"):
            client._raise_for_status(_FakeResponse(401))

    def test_raise_for_status_rewrites_forbidden_message(self) -> None:
        client = RunPodClient(api_key="bad-key")
        with self.assertRaisesRegex(RuntimeError, "does not have permission"):
            client._raise_for_status(_FakeResponse(403))

    def test_alphafold2_runpod_client_retries_missing_pdb_outputs_with_fresh_job(self) -> None:
        class _FakeRunPod:
            def __init__(self) -> None:
                self.wait_calls: list[tuple[str, str]] = []
                self.run_calls: list[dict[str, object]] = []

            def wait(self, endpoint_id: str, job_id: str) -> dict[str, object]:
                self.wait_calls.append((endpoint_id, job_id))
                return _missing_pdb_job()

            def run_and_wait_with_job_id(self, endpoint_id: str, payload: dict[str, object], on_job_id=None):  # type: ignore[no-untyped-def]
                self.run_calls.append({"endpoint_id": endpoint_id, "payload": dict(payload)})
                job_id = f"fresh_job_{len(self.run_calls)}"
                if callable(on_job_id):
                    on_job_id(job_id)
                return job_id, _successful_af2_job()

        observed_job_ids: list[tuple[str, str]] = []
        runpod = _FakeRunPod()
        client = AlphaFold2RunPodClient(runpod=runpod, endpoint_id="endpoint-1")

        out = client.predict(
            [SequenceRecord(id="seq1", sequence="ACDE")],
            on_job_id=lambda seq_id, job_id: observed_job_ids.append((seq_id, job_id)),
            resume_job_ids={"seq1": "stale_job_1"},
        )

        self.assertEqual(runpod.wait_calls, [("endpoint-1", "stale_job_1")])
        self.assertEqual(len(runpod.run_calls), 1)
        self.assertEqual(observed_job_ids, [("seq1", "stale_job_1"), ("seq1", "fresh_job_1")])
        self.assertEqual(out["seq1"]["best_model"], "model_1")
        self.assertIn("MODEL 1", str(out["seq1"]["ranked_0_pdb"] or ""))


if __name__ == "__main__":
    unittest.main()
