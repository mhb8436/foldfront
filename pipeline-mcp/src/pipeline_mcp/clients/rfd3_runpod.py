from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

import requests

from .runpod import RunPodClient


@dataclass(frozen=True)
class RFD3RunPodClient:
    runpod: RunPodClient
    endpoint_id: str

    def design(
        self,
        *,
        inputs: dict[str, Any] | None = None,
        inputs_text: str | None = None,
        input_files: dict[str, str] | None = None,
        cli_args: str | None = None,
        env: dict[str, str] | None = None,
        select_index: int = 0,
        max_return_designs: int | None = None,
        return_designs_pdb: bool = False,
        min_return_design_pdbs: int | None = None,
        resume_job_id: str | None = None,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if inputs_text is not None:
            payload["inputs_text"] = inputs_text
        if inputs is not None:
            payload["inputs"] = inputs
        if input_files is not None:
            payload["input_files"] = input_files
        if cli_args:
            payload["cli_args"] = str(cli_args)
        if env is not None:
            payload["env"] = env
        payload["select_index"] = int(select_index)
        if max_return_designs is not None:
            payload["max_return_designs"] = int(max_return_designs)
        payload["return_designs_pdb"] = bool(return_designs_pdb)
        if min_return_design_pdbs is not None:
            payload["min_return_design_pdbs"] = int(max(0, min_return_design_pdbs))
        payload["return_pdb"] = True
        payload["return_selected_json"] = False

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
            raise RuntimeError(f"RFD3 RunPod job not completed: {result}")
        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"RFD3 output missing/invalid: {result}")

        status = output.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed"}:
            msg = output.get("message") or output.get("details") or output
            raise RuntimeError(f"RFD3 endpoint error: {msg}")

        return output
