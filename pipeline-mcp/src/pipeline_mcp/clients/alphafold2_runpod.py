from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import tarfile
from typing import Any
from collections.abc import Callable

import requests

from ..af2_utils import af2_error_is_missing_pdb_outputs
from .runpod import RunPodClient
from ..models import SequenceRecord


def _archive_entries(output: dict[str, Any]) -> list[dict[str, str]]:
    status = output.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed"}:
        message = output.get("message")
        details = output.get("details")
        parts: list[str] = []
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        if isinstance(details, str) and details.strip():
            parts.append(details.strip())
        extra = "\n\n".join(parts) if parts else repr(output)
        raise RuntimeError(f"AlphaFold2 endpoint reported an error:\n{extra}")

    archives = output.get("archives")
    if isinstance(archives, list) and archives:
        out: list[dict[str, str]] = []
        for item in archives:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            b64 = item.get("base64")
            if isinstance(name, str) and isinstance(b64, str) and name and b64:
                out.append({"name": name, "base64": b64})
        if out:
            return out

    b64 = output.get("archive_base64")
    if isinstance(b64, str) and b64:
        return [{"name": "alphafold_results.tar.gz", "base64": b64}]

    raise RuntimeError("AlphaFold2 output did not include 'archives' or 'archive_base64'")


def _extract_member_text(tar: tarfile.TarFile, suffix: str) -> str | None:
    for member in tar.getmembers():
        if not member.isfile():
            continue
        if member.name.endswith(suffix):
            f = tar.extractfile(member)
            if f is None:
                continue
            raw = f.read()
            return raw.decode("utf-8", errors="replace")
    return None


def _best_plddt_from_ranking_debug(ranking: dict[str, Any]) -> tuple[str | None, float]:
    plddts = ranking.get("plddts")
    if isinstance(plddts, dict) and plddts:
        order = ranking.get("order")
        if isinstance(order, list) and order:
            first = order[0]
            if isinstance(first, str) and isinstance(plddts.get(first), (int, float)):
                return first, float(plddts[first])
        best_model = None
        best_score = float("-inf")
        for k, v in plddts.items():
            if not isinstance(k, str) or not isinstance(v, (int, float)):
                continue
            score = float(v)
            if score > best_score:
                best_score = score
                best_model = k
        if best_model is None:
            raise RuntimeError("ranking_debug.plddts contained no numeric scores")
        return best_model, float(best_score)

    for key in ("mean_plddt", "plddt", "avg_plddt"):
        v = ranking.get(key)
        if isinstance(v, (int, float)):
            return None, float(v)

    raise RuntimeError(f"Unable to extract pLDDT from ranking_debug keys: {sorted(ranking.keys())}")


@dataclass(frozen=True)
class AlphaFold2RunPodClient:
    runpod: RunPodClient
    endpoint_id: str

    def predict(
        self,
        sequences: list[SequenceRecord],
        *,
        model_preset: str = "monomer",
        db_preset: str = "full_dbs",
        max_template_date: str = "2020-05-14",
        extra_flags: str | None = None,
        on_job_id: Callable[[str, str], None] | None = None,
        resume_job_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for seq in sequences:
            payload: dict[str, Any] = {
                "sequence": seq.sequence,
                "model_preset": model_preset,
                "db_preset": db_preset,
                "max_template_date": max_template_date,
            }
            if extra_flags:
                payload["alphafold_extra_flags"] = str(extra_flags)

            existing_job_id = (resume_job_ids or {}).get(seq.id) if resume_job_ids else None
            for attempt in range(2):
                try:
                    if isinstance(existing_job_id, str) and existing_job_id.strip():
                        job_id = existing_job_id.strip()
                        if on_job_id is not None:
                            on_job_id(seq.id, job_id)
                        try:
                            job = self.runpod.wait(self.endpoint_id, job_id)
                        except requests.HTTPError as exc:
                            status_code = exc.response.status_code if exc.response is not None else None
                            if status_code == 404:
                                _, job = self.runpod.run_and_wait_with_job_id(
                                    self.endpoint_id,
                                    payload,
                                    on_job_id=(lambda job_id, seq_id=seq.id: on_job_id(seq_id, job_id)) if on_job_id else None,
                                )
                            else:
                                raise
                    else:
                        _, job = self.runpod.run_and_wait_with_job_id(
                            self.endpoint_id,
                            payload,
                            on_job_id=(lambda job_id, seq_id=seq.id: on_job_id(seq_id, job_id)) if on_job_id else None,
                        )
                    if job.get("status") not in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
                        raise RuntimeError(f"AlphaFold2 RunPod job not completed: {job}")

                    output = job.get("output")
                    if not isinstance(output, dict):
                        raise RuntimeError(f"AlphaFold2 output missing/invalid: {job}")

                    entries = _archive_entries(output)
                    if len(entries) != 1:
                        raise RuntimeError(
                            "AlphaFold2 endpoint returned multiple archives for a single sequence; "
                            "use an input archive for batch mode."
                        )

                    archive_name = entries[0]["name"]
                    archive_bytes = base64.b64decode(entries[0]["base64"])
                    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
                        ranking_text = _extract_member_text(tar, "ranking_debug.json")
                        if ranking_text is None:
                            raise RuntimeError("ranking_debug.json not found in AlphaFold2 archive")
                        ranking = json.loads(ranking_text)
                        if not isinstance(ranking, dict):
                            raise RuntimeError("ranking_debug.json is not an object")

                        ranked0 = _extract_member_text(tar, "ranked_0.pdb")
                        best_model, best_plddt = _best_plddt_from_ranking_debug(ranking)

                    results[seq.id] = {
                        "archive_name": archive_name,
                        "best_model": best_model,
                        "best_plddt": best_plddt,
                        "ranking_debug": ranking,
                        "ranked_0_pdb": ranked0,
                        "files": output.get("files"),
                    }
                    break
                except Exception as exc:
                    if attempt >= 1 or not af2_error_is_missing_pdb_outputs(str(exc)):
                        raise
                    existing_job_id = None
        return results
