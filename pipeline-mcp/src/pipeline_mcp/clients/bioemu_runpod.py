from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

import requests

from .runpod import RunPodClient


@dataclass(frozen=True)
class BioEmuRunPodClient:
    runpod: RunPodClient
    endpoint_id: str

    def sample(
        self,
        *,
        sequence: str,
        num_samples: int = 10,
        batch_size_100: int | None = None,
        model_name: str = "bioemu-v1.1",
        filter_samples: bool = True,
        base_seed: int | None = None,
        steering_config_text: str | None = None,
        env: dict[str, str] | None = None,
        return_pdb: bool = True,
        return_sample_pdbs: bool = True,
        max_return_sample_pdbs: int = 10,
        min_return_sample_pdbs: int | None = None,
        resume_job_id: str | None = None,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        # Append a newline to the sequence. The RunPod server has a bug where it treats
        # pure sequences without newlines as potential filenames and crashes on long sequences
        # (OSError: [Errno 36] File name too long). Adding a newline forces it to write to sequence.fasta.
        safe_sequence = str(sequence).strip()
        if not safe_sequence.startswith(">"):
            safe_sequence = f">target\n{safe_sequence}"

        payload: dict[str, Any] = {
            "sequence": safe_sequence,
            "num_samples": int(max(1, num_samples)),
            "model_name": str(model_name or "bioemu-v1.1"),
            "filter_samples": bool(filter_samples),
            "return_pdb": bool(return_pdb),
            "return_sample_pdbs": bool(return_sample_pdbs),
            "max_return_sample_pdbs": int(max(1, max_return_sample_pdbs)),
        }
        if min_return_sample_pdbs is not None:
            payload["min_return_sample_pdbs"] = int(max(0, min_return_sample_pdbs))
        if batch_size_100 is not None:
            payload["batch_size_100"] = int(batch_size_100)
        if base_seed is not None:
            payload["base_seed"] = int(base_seed)
        if steering_config_text is not None:
            payload["steering_config_text"] = str(steering_config_text)
        if env:
            payload["env"] = dict(env)

        existing_job_id = str(resume_job_id or "").strip()
        if existing_job_id:
            if on_job_id is not None:
                on_job_id(existing_job_id)
            try:
                result = self.runpod.wait(self.endpoint_id, existing_job_id)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 404:
                    _, result = self.runpod.run_and_wait_with_job_id(
                        self.endpoint_id,
                        payload,
                        on_job_id=on_job_id,
                    )
                else:
                    raise
        else:
            _, result = self.runpod.run_and_wait_with_job_id(self.endpoint_id, payload, on_job_id=on_job_id)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(f"BioEmu RunPod job not completed: {result}")
        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"BioEmu output missing/invalid: {result}")

        status = output.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed"}:
            message = output.get("message") or output.get("details") or output
            raise RuntimeError(f"BioEmu endpoint error: {message}")

        return output
