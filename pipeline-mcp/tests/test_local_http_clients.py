from __future__ import annotations

import base64
import io
import json as json_module
import zipfile

import pytest

from pipeline_mcp.clients.local_http import LocalHTTPBioEmuClient
from pipeline_mcp.clients.local_http import LocalHTTPDiffDockClient
from pipeline_mcp.clients.local_http import LocalHTTPAlphaFold2Client
from pipeline_mcp.clients.local_http import LocalHTTPRFD3Client
from pipeline_mcp.models import SequenceRecord


class _Response:
    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_local_http_rfd3_does_not_send_callback_in_json_payload(monkeypatch):
    calls: list[dict] = []
    seen_job_ids: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        json_module.dumps(json)
        calls.append(json)
        return _Response({"status": "COMPLETED", "job_id": "rfd3-local-job", "output": {"selected_pdb": "ATOM\n"}})

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    result = LocalHTTPRFD3Client("http://gpu.example:18104").design(
        inputs={"spec-1": {"contig": "A1-10"}},
        on_job_id=seen_job_ids.append,
    )

    assert result["selected_pdb"] == "ATOM\n"
    assert "on_job_id" not in calls[0]["input"]
    assert seen_job_ids == ["rfd3-local-job"]


def test_local_http_af2_omits_inline_archive_payloads_from_results(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        json_module.dumps(json)
        return _Response(
            {
                "status": "COMPLETED",
                "job_id": "af2-local-job",
                "output": {
                    "results": {
                        "seq1": {
                            "best_plddt": 91.0,
                            "ranked_0_pdb": "MODEL 1\nENDMDL\n",
                            "archive_base64": "abc123",
                            "archives": [
                                {"name": "alphafold_results.tar.gz", "base64": "def456"}
                            ],
                        }
                    }
                },
            }
        )

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    result = LocalHTTPAlphaFold2Client("http://gpu.example:18160").predict(
        [SequenceRecord(id="seq1", sequence="ACDE")]
    )

    record = result["seq1"]
    assert record["best_plddt"] == 91.0
    assert record["ranked_0_pdb"] == "MODEL 1\nENDMDL\n"
    assert record["archive_base64"]["omitted"] is True
    assert record["archives"][0]["base64"]["omitted"] is True


def test_local_http_bioemu_does_not_send_callback_in_json_payload(monkeypatch):
    calls: list[dict] = []
    seen_job_ids: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        json_module.dumps(json)
        calls.append(json)
        return _Response({"status": "COMPLETED", "job_id": "bioemu-local-job", "output": {"samples": []}})

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    result = LocalHTTPBioEmuClient("http://gpu.example:18103").sample(
        sequence="ACDE",
        on_job_id=seen_job_ids.append,
    )

    assert result["samples"] == []
    assert "on_job_id" not in calls[0]["input"]
    assert seen_job_ids == ["bioemu-local-job"]


def test_local_http_surfaces_worker_error_body_on_500(monkeypatch):
    # Worker returns its real failure as JSON even with HTTP 500. The client must
    # raise that detail, not the opaque "500 Server Error ... for url" that
    # raise_for_status() would produce (which reads like a GPU outage).
    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        return _Response(
            {
                "ok": False,
                "status": "FAILED",
                "error": "Sequence contains non-valid protein character: X",
                "traceback": "AssertionError: ...",
            },
            status_code=500,
        )

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    with pytest.raises(RuntimeError) as excinfo:
        LocalHTTPRFD3Client("http://gpu.example:18104").design(inputs={"s": {}})

    message = str(excinfo.value)
    assert "Sequence contains non-valid protein character: X" in message
    assert "HTTP 500" in message


def test_local_http_bioemu_rejects_non_iupac_sequence_before_dispatch(monkeypatch):
    posted: list = []

    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        posted.append(json)
        return _Response({"status": "COMPLETED", "output": {"samples": []}})

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    with pytest.raises(ValueError) as excinfo:
        LocalHTTPBioEmuClient("http://gpu.example:18103").sample(sequence="ACDEXGHIK")

    message = str(excinfo.value)
    assert "BioEmu" in message
    assert "'X'" in message or "X" in message
    assert "position(s) 5" in message
    # Must fail fast without ever reaching the GPU worker.
    assert posted == []


def test_local_http_diffdock_does_not_send_callback_in_json_payload(monkeypatch):
    calls: list[dict] = []
    seen_job_ids: list[str] = []
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("smoke_diffdock/rank1.sdf", "rank1 sdf text")
    zip_b64 = base64.b64encode(zip_buffer.getvalue()).decode("ascii")

    def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
        json_module.dumps(json)
        calls.append(json)
        return _Response(
            {
                "status": "COMPLETED",
                "job_id": "diffdock-local-job",
                "output": {"returncode": 0, "out_dir_zip_b64": zip_b64},
            }
        )

    monkeypatch.setattr("pipeline_mcp.clients.local_http.requests.post", fake_post)

    result = LocalHTTPDiffDockClient("http://gpu.example:18105").dock(
        protein_pdb="ATOM\n",
        ligand_smiles="CCO",
        complex_name="smoke_diffdock",
        on_job_id=seen_job_ids.append,
    )

    assert result["sdf_text"] == "rank1 sdf text"
    assert result["selected_sdf_name"] == "smoke_diffdock/rank1.sdf"
    assert "protein_ligand_csv" in calls[0]["input"]
    assert calls[0]["input"]["pdb_files"][0]["filename"] == "smoke_diffdock.pdb"
    assert "on_job_id" not in calls[0]["input"]
    assert seen_job_ids == ["diffdock-local-job"]
