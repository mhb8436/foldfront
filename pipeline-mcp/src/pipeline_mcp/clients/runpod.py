from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any
from collections.abc import Callable

import requests

_RUNPOD_JOB_API_BASE = "https://api.runpod.ai/v2"
_RUNPOD_REST_API_BASE = "https://rest.runpod.io/v1"


def _status_message(status_code: int | None) -> str | None:
    if status_code == 401:
        return "RUNPOD_API_KEY was rejected by the RunPod API. Update the key and restart pipeline-mcp."
    if status_code == 403:
        return "RUNPOD_API_KEY does not have permission for the requested RunPod API action."
    return None


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def requests_verify_arg(*, ca_bundle: str | None, skip_verify: bool) -> bool | str:
    if skip_verify or _env_true("RUNPOD_INSECURE"):
        return False
    if ca_bundle:
        return ca_bundle
    return True


@dataclass(frozen=True)
class RunPodClient:
    api_key: str
    ca_bundle: str | None = None
    skip_verify: bool = False
    timeout_s: float = 60.0
    poll_interval_s: float = 2.0
    # Optional best-effort hook fired with (endpoint_id, job_data) when a job
    # reaches COMPLETED; used to record stage durations for queue-ETA. Never
    # allowed to affect the job result (errors are swallowed).
    on_job_complete: Callable[[str, dict[str, Any]], None] | None = None

    def _raise_for_status(self, response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            message = _status_message(exc.response.status_code if exc.response is not None else None)
            if message:
                raise RuntimeError(message) from exc
            raise

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _rest(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{_RUNPOD_REST_API_BASE}{path}"
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=self.timeout_s,
            verify=requests_verify_arg(ca_bundle=self.ca_bundle, skip_verify=self.skip_verify),
        )
        self._raise_for_status(response)
        if not response.content:
            return {}
        return response.json()

    def run(self, endpoint_id: str, input_payload: dict[str, Any]) -> str:
        url = f"{_RUNPOD_JOB_API_BASE}/{endpoint_id}/run"
        r = requests.post(
            url,
            headers=self._headers(),
            json={"input": input_payload},
            timeout=self.timeout_s,
            verify=requests_verify_arg(ca_bundle=self.ca_bundle, skip_verify=self.skip_verify),
        )
        self._raise_for_status(r)
        data = r.json()
        job_id = data.get("id")
        if not job_id:
            raise RuntimeError(f"RunPod response missing job id: {data}")
        return str(job_id)

    def status(self, endpoint_id: str, job_id: str) -> dict[str, Any]:
        url = f"{_RUNPOD_JOB_API_BASE}/{endpoint_id}/status/{job_id}"
        r = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_s,
            verify=requests_verify_arg(ca_bundle=self.ca_bundle, skip_verify=self.skip_verify),
        )
        self._raise_for_status(r)
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected RunPod status response: {data!r}")
        return data

    def cancel(self, endpoint_id: str, job_id: str) -> dict[str, Any]:
        url = f"{_RUNPOD_JOB_API_BASE}/{endpoint_id}/cancel/{job_id}"
        r = requests.post(
            url,
            headers=self._headers(),
            timeout=self.timeout_s,
            verify=requests_verify_arg(ca_bundle=self.ca_bundle, skip_verify=self.skip_verify),
        )
        self._raise_for_status(r)
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected RunPod cancel response: {data!r}")
        return data

    def health(self, endpoint_id: str) -> dict[str, Any]:
        url = f"{_RUNPOD_JOB_API_BASE}/{endpoint_id}/health"
        r = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_s,
            verify=requests_verify_arg(ca_bundle=self.ca_bundle, skip_verify=self.skip_verify),
        )
        self._raise_for_status(r)
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected RunPod health response: {data!r}")
        return data

    def wait(self, endpoint_id: str, job_id: str) -> dict[str, Any]:
        start = time.monotonic()
        transient_failures = 0
        while True:
            try:
                data = self.status(endpoint_id, job_id)
                transient_failures = 0
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 500, 502, 503, 504}:
                    transient_failures += 1
                    delay = min(self.poll_interval_s * (2**min(transient_failures, 6)), 60.0)
                    time.sleep(delay)
                    continue
                raise
            except (requests.Timeout, requests.ConnectionError, ValueError):
                transient_failures += 1
                delay = min(self.poll_interval_s * (2**min(transient_failures, 6)), 60.0)
                time.sleep(delay)
                continue
            status = data.get("status") or data.get("state")
            if status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "TIMED_OUT"}:
                if status == "COMPLETED" and self.on_job_complete is not None:
                    try:
                        self.on_job_complete(endpoint_id, data)
                    except Exception:
                        pass
                return data
            elapsed = time.monotonic() - start
            if elapsed > 60 * 60 * 6:
                raise TimeoutError(f"RunPod job timeout (>6h): endpoint={endpoint_id} job_id={job_id}")
            time.sleep(self.poll_interval_s)

    def run_and_wait(self, endpoint_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_and_wait_with_job_id(endpoint_id, input_payload)[1]

    def run_and_wait_with_job_id(
        self,
        endpoint_id: str,
        input_payload: dict[str, Any],
        *,
        on_job_id: Callable[[str], None] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        job_id = self.run(endpoint_id, input_payload)
        if on_job_id is not None:
            on_job_id(job_id)
        data = self.wait(endpoint_id, job_id)
        return job_id, data

    def list_endpoints(self, *, include_workers: bool = False, include_template: bool = True) -> Any:
        params = {
            "includeWorkers": "true" if include_workers else "false",
            "includeTemplate": "true" if include_template else "false",
        }
        return self._rest("GET", "/endpoints", params=params)

    def get_endpoint(self, endpoint_id: str, *, include_workers: bool = True, include_template: bool = True) -> dict[str, Any]:
        params = {
            "includeWorkers": "true" if include_workers else "false",
            "includeTemplate": "true" if include_template else "false",
        }
        data = self._rest("GET", f"/endpoints/{endpoint_id}", params=params)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected RunPod endpoint response: {data!r}")
        return data

    def update_endpoint(self, endpoint_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._rest("PATCH", f"/endpoints/{endpoint_id}", json_body=patch)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected RunPod endpoint update response: {data!r}")
        return data

    def list_endpoint_billing(
        self,
        *,
        start_time: str,
        end_time: str,
        bucket_size: str = "day",
        endpoint_id: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "startTime": start_time,
            "endTime": end_time,
            "bucketSize": bucket_size,
        }
        if endpoint_id:
            params["endpointId"] = endpoint_id
        return self._rest("GET", "/billing/endpoints", params=params)
