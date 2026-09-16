from __future__ import annotations

from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from .clients.runpod import RunPodClient
from .pipeline import PipelineRunner
from .runpod_metrics import get_runpod_metrics_store


_MANAGED_SERVICE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("mmseqs", "MMseqs", "mmseqs", "MMSEQS_ENDPOINT_ID"),
    ("proteinmpnn", "ProteinMPNN", "proteinmpnn", "PROTEINMPNN_ENDPOINT_ID"),
    ("colabfold", "ColabFold", "colabfold", "COLABFOLD_ENDPOINT_ID"),
    ("alphafold2", "AlphaFold2", "af2", "ALPHAFOLD2_ENDPOINT_ID"),
    ("rfd3", "RFD3", "rfd3", "RFD3_ENDPOINT_ID"),
    ("bioemu", "BioEmu", "bioemu", "BIOEMU_ENDPOINT_ID"),
    ("diffdock", "DiffDock", "diffdock", "DIFFDOCK_ENDPOINT_ID"),
    ("relax", "Rosetta Relax", "rosetta_relax", "RUNPOD_RELAX_ENDPOINT_ID"),
)

_RUNPOD_ENDPOINT_PATCH_FIELDS = {
    "name",
    "gpuTypeIds",
    "dataCenterIds",
    "idleTimeout",
    "executionTimeoutMs",
    "flashBoot",
    "scalerType",
    "scalerValue",
    "templateId",
    "networkVolumeId",
    "workersMin",
    "workersMax",
}


_RUNPOD_HISTORY_SNAPSHOT_LIMIT = 120


def _history_sample_from_endpoint(endpoint: dict[str, Any], *, captured_at: str, mode: str) -> dict[str, Any] | None:
    endpoint_id = str(endpoint.get("id") or "").strip()
    if not endpoint_id:
        return None
    worker_summary = endpoint.get("worker_summary") if isinstance(endpoint.get("worker_summary"), dict) else {}
    states = worker_summary.get("states") if isinstance(worker_summary.get("states"), dict) else {}
    health_jobs = endpoint.get("health_jobs") if isinstance(endpoint.get("health_jobs"), dict) else {}

    def _int_value(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                return int(float(value.strip()))
            except ValueError:
                return 0
        return 0

    workers = _int_value(worker_summary.get("total"))
    queued = _int_value(health_jobs.get("in_queue"))
    running = _int_value(health_jobs.get("in_progress"))
    if not running and states:
        running = _int_value(states.get("running"))
    sample = {
        "t": captured_at,
        "workers": workers,
        "queued": queued,
        "running": running,
        "completed": _int_value(health_jobs.get("completed")),
        "failed": _int_value(health_jobs.get("failed")),
        "retried": _int_value(health_jobs.get("retried")),
        "mode": mode,
    }
    return {"endpoint_id": endpoint_id, **sample}


def _store_usage_history(output_root: str, endpoints: list[dict[str, Any]], *, captured_at: str, mode: str) -> dict[str, list[dict[str, Any]]]:
    store = get_runpod_metrics_store(output_root)
    samples = [
        sample
        for sample in (
            _history_sample_from_endpoint(endpoint, captured_at=captured_at, mode=mode)
            for endpoint in endpoints
        )
        if sample is not None
    ]
    if samples:
        store.record_usage_samples(samples)
    return _load_usage_history(output_root, endpoint_ids=[str(item.get("id") or "") for item in endpoints if str(item.get("id") or "").strip()])


def _history_from_store_or_snapshot(output_root: str, endpoints: list[dict[str, Any]], *, captured_at: str, mode: str) -> dict[str, list[dict[str, Any]]]:
    store = get_runpod_metrics_store(output_root)
    if store.get_state("collector_started_at", "").strip():
        return _load_usage_history(output_root, endpoint_ids=[str(item.get("id") or "") for item in endpoints if str(item.get("id") or "").strip()])
    return _store_usage_history(output_root, endpoints, captured_at=captured_at, mode=mode)


def _load_usage_history(
    output_root: str,
    *,
    endpoint_ids: list[str] | None = None,
    snapshot_limit: int = _RUNPOD_HISTORY_SNAPSHOT_LIMIT,
) -> dict[str, list[dict[str, Any]]]:
    store = get_runpod_metrics_store(output_root)
    history, _resolution = store.read_usage_history(
        endpoint_ids=endpoint_ids,
        days=30,
        resolution="raw",
        limit=max(int(snapshot_limit), 1),
    )
    return history


def _first_str(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    return None


def _first_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value.strip())
            except ValueError:
                continue
    return None


def _coerce_int(value: object, *, name: str, min_value: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        out = value
    elif isinstance(value, float):
        out = int(value)
    elif isinstance(value, str) and value.strip():
        out = int(float(value.strip()))
    else:
        raise ValueError(f"{name} must be an integer")
    if min_value is not None and out < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return out


def _coerce_string_list(value: object, *, name: str, allow_empty: bool = False) -> list[str]:
    raw_items: list[object]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [part.strip() for part in value.split(",")]
    else:
        raise ValueError(f"{name} must be a list of strings")
    items = [str(item).strip() for item in raw_items if str(item).strip()]
    if not items and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    return items


def _string_list_from_payload(value: object | None) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_endpoint_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("endpoints", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if payload.get("id") or payload.get("endpointId"):
            return [payload]
    return []


def _extract_billing_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("billingHistory", "records", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_endpoint_id(client: object | None) -> str:
    if client is None:
        return ""
    endpoint_id = getattr(client, "endpoint_id", None)
    return str(endpoint_id or "").strip()


def _extract_runpod_client(runner: PipelineRunner) -> RunPodClient | None:
    for attr in ("mmseqs", "proteinmpnn", "colabfold", "af2", "rfd3", "bioemu", "diffdock", "rosetta_relax"):
        client = getattr(runner, attr, None)
        runpod = getattr(client, "runpod", None)
        if isinstance(runpod, RunPodClient):
            return runpod
    return None


def _is_admin_api_permission_error(exc: Exception) -> bool:
    message = str(exc or "")
    return "RUNPOD_API_KEY was rejected" in message or "does not have permission" in message


def _normalize_health_jobs(payload: dict[str, Any]) -> dict[str, int]:
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), dict) else {}

    def _value(name: str) -> int:
        raw = jobs.get(name)
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                return int(float(raw))
            except ValueError:
                return 0
        return 0

    return {
        "completed": _value("completed"),
        "failed": _value("failed"),
        "in_progress": _value("inProgress"),
        "in_queue": _value("inQueue"),
        "retried": _value("retried"),
    }


def _normalize_health_worker_summary(payload: dict[str, Any]) -> dict[str, Any]:
    workers = payload.get("workers") if isinstance(payload.get("workers"), dict) else {}
    states: dict[str, int] = {}
    total = 0
    for key, value in workers.items():
        count = 0
        if isinstance(value, (int, float)):
            count = int(value)
        elif isinstance(value, str) and value.strip():
            try:
                count = int(float(value))
            except ValueError:
                count = 0
        states[str(key)] = count
        total += count
    return {"total": total, "states": states, "hourly_cost": None}


def _fallback_endpoint_from_health(
    service: dict[str, str],
    managed_services: list[dict[str, str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    endpoint_id = str(service.get("endpoint_id") or "").strip()
    label = str(service.get("label") or service.get("key") or endpoint_id or "RunPod")
    return {
        "id": endpoint_id,
        "name": f"{label} (health)",
        "compute_type": "serverless",
        "gpu_types": [],
        "data_center_ids": [],
        "network_volume_id": "",
        "workers_min": None,
        "workers_max": None,
        "scaler_type": "",
        "scaler_value": None,
        "idle_timeout": None,
        "execution_timeout_ms": None,
        "flash_boot": None,
        "managed": bool(managed_services),
        "managed_services": list(managed_services),
        "template": {"id": "", "name": "", "image_name": ""},
        "worker_summary": _normalize_health_worker_summary(payload),
        "workers": [],
        "health_jobs": _normalize_health_jobs(payload),
        "read_only": True,
        "data_source": "health",
    }


def collect_managed_endpoint_refs(runner: PipelineRunner) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    by_endpoint: dict[str, list[dict[str, str]]] = defaultdict(list)
    services: list[dict[str, str]] = []
    for key, label, attr, env_var in _MANAGED_SERVICE_SPECS:
        endpoint_id = _extract_endpoint_id(getattr(runner, attr, None))
        service = {
            "key": key,
            "label": label,
            "env_var": env_var,
            "endpoint_id": endpoint_id,
            "configured": bool(endpoint_id),
        }
        services.append(service)
        if endpoint_id:
            by_endpoint[endpoint_id].append({"key": key, "label": label, "env_var": env_var})
    return dict(by_endpoint), services


def _collect_gpu_labels(endpoint: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("gpuTypeIds", "gpuTypes", "gpuIds", "gpuDisplayNames"):
        values.extend(_string_list_from_payload(endpoint.get(key)))
    template = endpoint.get("template")
    if isinstance(template, dict):
        for key in ("gpuTypeIds", "gpuTypes", "gpuIds", "gpuDisplayNames"):
            values.extend(_string_list_from_payload(template.get(key)))
    return sorted({value for value in values if value})


def _collect_data_centers(endpoint: dict[str, Any]) -> list[str]:
    values = _string_list_from_payload(endpoint.get("dataCenterIds"))
    if values:
        return sorted(set(values))
    template = endpoint.get("template")
    if isinstance(template, dict):
        template_values = _string_list_from_payload(template.get("dataCenterIds"))
        if template_values:
            return sorted(set(template_values))
    return []


def _normalize_worker(worker: dict[str, Any]) -> dict[str, Any]:
    status = _first_str(worker, "desiredStatus", "status", "state", "lastStatus") or "unknown"
    cost_per_hr = _first_number(worker, "costPerHr", "cost_per_hr")
    return {
        "id": _first_str(worker, "id", "workerId", "podId"),
        "name": _first_str(worker, "name"),
        "status": status,
        "gpu_types": sorted(
            set(
                _string_list_from_payload(worker.get("gpuTypeIds"))
                + _string_list_from_payload(worker.get("gpuTypes"))
                + _string_list_from_payload(worker.get("gpuDisplayNames"))
            )
        ),
        "cost_per_hr": cost_per_hr,
        "raw": worker,
    }


def _summarize_workers(workers: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    hourly_cost = 0.0
    seen_hourly = False
    for worker in workers:
        status = str(worker.get("status") or "unknown").strip().lower() or "unknown"
        counts[status] += 1
        cost_per_hr = worker.get("cost_per_hr")
        if isinstance(cost_per_hr, (int, float)):
            hourly_cost += float(cost_per_hr)
            seen_hourly = True
    return {
        "total": len(workers),
        "states": dict(sorted(counts.items())),
        "hourly_cost": round(hourly_cost, 4) if seen_hourly else None,
    }


def normalize_runpod_endpoint(
    endpoint: dict[str, Any],
    *,
    managed_services: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    managed_refs = list(managed_services or [])
    template = endpoint.get("template") if isinstance(endpoint.get("template"), dict) else {}
    workers_raw = endpoint.get("workers") if isinstance(endpoint.get("workers"), list) else []
    workers = [_normalize_worker(item) for item in workers_raw if isinstance(item, dict)]
    worker_summary = _summarize_workers(workers)
    workers_min = _first_number(endpoint, "workersMin", "workers_min")
    workers_max = _first_number(endpoint, "workersMax", "workers_max")
    scaler_value = _first_number(endpoint, "scalerValue", "scaler_value")
    idle_timeout = _first_number(endpoint, "idleTimeout", "idle_timeout")
    execution_timeout_ms = _first_number(endpoint, "executionTimeoutMs", "execution_timeout_ms")

    return {
        "id": _first_str(endpoint, "id", "endpointId"),
        "name": _first_str(endpoint, "name") or _first_str(template, "name"),
        "compute_type": _first_str(endpoint, "computeType", "compute_type"),
        "gpu_types": _collect_gpu_labels(endpoint),
        "data_center_ids": _collect_data_centers(endpoint),
        "network_volume_id": _first_str(endpoint, "networkVolumeId", "network_volume_id"),
        "workers_min": int(workers_min) if workers_min is not None else None,
        "workers_max": int(workers_max) if workers_max is not None else None,
        "scaler_type": _first_str(endpoint, "scalerType", "scaler_type"),
        "scaler_value": int(scaler_value) if scaler_value is not None else None,
        "idle_timeout": int(idle_timeout) if idle_timeout is not None else None,
        "execution_timeout_ms": int(execution_timeout_ms) if execution_timeout_ms is not None else None,
        "flash_boot": _first_bool(endpoint, "flashBoot", "flash_boot"),
        "managed": bool(managed_refs),
        "managed_services": managed_refs,
        "template": {
            "id": _first_str(template, "id", "templateId"),
            "name": _first_str(template, "name"),
            "image_name": _first_str(template, "imageName", "image"),
        },
        "worker_summary": worker_summary,
        "workers": workers,
    }


def _summarize_endpoints(
    endpoints: list[dict[str, Any]],
    *,
    missing_endpoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_workers_min = 0
    total_workers_max = 0
    active_workers = 0
    gpu_types: set[str] = set()
    managed_count = 0

    for endpoint in endpoints:
        if endpoint.get("managed"):
            managed_count += 1
        workers_min = endpoint.get("workers_min")
        workers_max = endpoint.get("workers_max")
        if isinstance(workers_min, int):
            total_workers_min += workers_min
        if isinstance(workers_max, int):
            total_workers_max += workers_max
        worker_summary = endpoint.get("worker_summary")
        if isinstance(worker_summary, dict):
            total = worker_summary.get("total")
            if isinstance(total, int):
                active_workers += total
        gpu_types.update(str(item) for item in (endpoint.get("gpu_types") or []) if str(item).strip())

    return {
        "total_endpoints": len(endpoints),
        "managed_endpoints": managed_count,
        "missing_managed_endpoints": len(missing_endpoints or []),
        "total_workers_min": total_workers_min,
        "total_workers_max": total_workers_max,
        "active_workers": active_workers,
        "gpu_type_count": len(gpu_types),
    }


def _normalize_billing_record(record: dict[str, Any], managed_endpoint_map: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    endpoint_id = _first_str(record, "endpointId", "endpoint_id")
    timestamp = _first_str(record, "bucketStart", "startTime", "date", "timestamp", "time")
    cost = _first_number(record, "cost", "totalCost", "amount", "billingAmount")
    gpu_type_id = _first_str(record, "gpuTypeId", "gpu_type_id")
    data_center_id = _first_str(record, "dataCenterId", "data_center_id")
    worker_id = _first_str(record, "workerId", "worker_id", "podId")
    return {
        "endpoint_id": endpoint_id,
        "timestamp": timestamp,
        "cost": round(cost, 6) if cost is not None else 0.0,
        "gpu_type_id": gpu_type_id,
        "data_center_id": data_center_id,
        "worker_id": worker_id,
        "managed_services": list(managed_endpoint_map.get(endpoint_id, [])),
    }


def _summarize_billing(records: list[dict[str, Any]], managed_endpoint_map: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    total_cost = 0.0
    by_endpoint: dict[str, dict[str, Any]] = {}
    for record in records:
        cost = float(record.get("cost") or 0.0)
        total_cost += cost
        endpoint_id = str(record.get("endpoint_id") or "").strip()
        if not endpoint_id:
            continue
        bucket = by_endpoint.setdefault(
            endpoint_id,
            {
                "endpoint_id": endpoint_id,
                "cost": 0.0,
                "records": 0,
                "managed_services": list(managed_endpoint_map.get(endpoint_id, [])),
            },
        )
        bucket["cost"] = round(float(bucket["cost"]) + cost, 6)
        bucket["records"] = int(bucket["records"]) + 1
    ranked = sorted(by_endpoint.values(), key=lambda item: float(item.get("cost") or 0.0), reverse=True)
    return {
        "total_cost": round(total_cost, 6),
        "records": len(records),
        "by_endpoint": ranked,
    }


def sanitize_runpod_endpoint_patch(patch: object) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    unknown = sorted(set(patch) - _RUNPOD_ENDPOINT_PATCH_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported RunPod endpoint patch fields: {', '.join(unknown)}")
    cleaned: dict[str, Any] = {}

    if "name" in patch:
        name = str(patch.get("name") or "").strip()
        if not name:
            raise ValueError("name must not be empty")
        cleaned["name"] = name
    if "gpuTypeIds" in patch:
        cleaned["gpuTypeIds"] = _coerce_string_list(patch.get("gpuTypeIds"), name="gpuTypeIds")
    if "dataCenterIds" in patch:
        cleaned["dataCenterIds"] = _coerce_string_list(
            patch.get("dataCenterIds"),
            name="dataCenterIds",
            allow_empty=True,
        )
    if "idleTimeout" in patch:
        cleaned["idleTimeout"] = _coerce_int(patch.get("idleTimeout"), name="idleTimeout", min_value=0)
    if "executionTimeoutMs" in patch:
        cleaned["executionTimeoutMs"] = _coerce_int(
            patch.get("executionTimeoutMs"),
            name="executionTimeoutMs",
            min_value=0,
        )
    if "flashBoot" in patch:
        value = patch.get("flashBoot")
        if not isinstance(value, bool):
            raise ValueError("flashBoot must be a boolean")
        cleaned["flashBoot"] = value
    if "scalerType" in patch:
        scaler_type = str(patch.get("scalerType") or "").strip()
        if not scaler_type:
            raise ValueError("scalerType must not be empty")
        cleaned["scalerType"] = scaler_type
    if "scalerValue" in patch:
        cleaned["scalerValue"] = _coerce_int(patch.get("scalerValue"), name="scalerValue", min_value=0)
    if "templateId" in patch:
        template_id = str(patch.get("templateId") or "").strip()
        if not template_id:
            raise ValueError("templateId must not be empty")
        cleaned["templateId"] = template_id
    if "networkVolumeId" in patch:
        value = patch.get("networkVolumeId")
        if value is None:
            cleaned["networkVolumeId"] = None
        else:
            cleaned["networkVolumeId"] = str(value).strip() or None
    if "workersMin" in patch:
        cleaned["workersMin"] = _coerce_int(patch.get("workersMin"), name="workersMin", min_value=0)
    if "workersMax" in patch:
        cleaned["workersMax"] = _coerce_int(patch.get("workersMax"), name="workersMax", min_value=0)

    if "workersMin" in cleaned and "workersMax" in cleaned and cleaned["workersMin"] > cleaned["workersMax"]:
        raise ValueError("workersMin must be <= workersMax")
    if not cleaned:
        raise ValueError("patch must contain at least one supported field")
    return cleaned


@dataclass(frozen=True)
class RunPodAdminService:
    runpod: RunPodClient
    output_root: str
    managed_endpoint_map: dict[str, list[dict[str, str]]]
    managed_services: list[dict[str, str]]

    def _health_fallback_endpoints(self) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        for service in self.managed_services:
            endpoint_id = str(service.get("endpoint_id") or "").strip()
            if not endpoint_id:
                continue
            health = self.runpod.health(endpoint_id)
            endpoints.append(
                _fallback_endpoint_from_health(
                    service,
                    self.managed_endpoint_map.get(endpoint_id, []),
                    health,
                )
            )
        endpoints.sort(key=lambda item: str(item.get("name") or item.get("id") or "").lower())
        return endpoints

    def _health_fallback_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        service = next(
            (item for item in self.managed_services if str(item.get("endpoint_id") or "").strip() == endpoint_id),
            {"endpoint_id": endpoint_id, "label": endpoint_id, "key": endpoint_id},
        )
        health = self.runpod.health(endpoint_id)
        return _fallback_endpoint_from_health(service, self.managed_endpoint_map.get(endpoint_id, []), health)

    def list_endpoints(self, *, include_workers: bool = False) -> dict[str, Any]:
        try:
            payload = self.runpod.list_endpoints(include_workers=include_workers, include_template=True)
            endpoints = [
                normalize_runpod_endpoint(item, managed_services=self.managed_endpoint_map.get(_first_str(item, "id", "endpointId"), []))
                for item in _extract_endpoint_items(payload)
            ]
            endpoints.sort(key=lambda item: (not bool(item.get("managed")), str(item.get("name") or item.get("id") or "").lower()))
            seen_ids = {str(item.get("id") or "") for item in endpoints if str(item.get("id") or "")}
            missing = []
            for service in self.managed_services:
                endpoint_id = str(service.get("endpoint_id") or "").strip()
                if endpoint_id and endpoint_id not in seen_ids:
                    missing.append(
                        {
                            "endpoint_id": endpoint_id,
                            "managed_services": list(self.managed_endpoint_map.get(endpoint_id, [])),
                        }
                    )
            usage_history = _history_from_store_or_snapshot(
                self.output_root,
                endpoints,
                captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                mode="rest",
            )
            return {
                "endpoints": endpoints,
                "managed_services": list(self.managed_services),
                "missing_endpoints": missing,
                "summary": _summarize_endpoints(endpoints, missing_endpoints=missing),
                "usage_history": usage_history,
                "history_source": "server",
                "mode": "rest",
                "read_only": False,
            }
        except Exception as exc:
            if not _is_admin_api_permission_error(exc):
                raise
            endpoints = self._health_fallback_endpoints()
            usage_history = _history_from_store_or_snapshot(
                self.output_root,
                endpoints,
                captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                mode="health_fallback",
            )
            return {
                "endpoints": endpoints,
                "managed_services": list(self.managed_services),
                "missing_endpoints": [],
                "summary": _summarize_endpoints(endpoints, missing_endpoints=[]),
                "usage_history": usage_history,
                "history_source": "server",
                "mode": "health_fallback",
                "read_only": True,
                "warnings": [str(exc), "Showing health-only monitoring from configured endpoint IDs."],
            }

    def get_endpoint(self, endpoint_id: str, *, include_workers: bool = True) -> dict[str, Any]:
        try:
            payload = self.runpod.get_endpoint(endpoint_id, include_workers=include_workers, include_template=True)
            endpoint = normalize_runpod_endpoint(payload, managed_services=self.managed_endpoint_map.get(endpoint_id, []))
            usage_history = _history_from_store_or_snapshot(
                self.output_root,
                [endpoint],
                captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                mode="rest",
            )
            return {
                "endpoint": endpoint,
                "usage_history": usage_history.get(endpoint_id, []),
                "history_source": "server",
                "mode": "rest",
                "read_only": False,
            }
        except Exception as exc:
            if not _is_admin_api_permission_error(exc):
                raise
            endpoint = self._health_fallback_endpoint(endpoint_id)
            usage_history = _history_from_store_or_snapshot(
                self.output_root,
                [endpoint],
                captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                mode="health_fallback",
            )
            return {
                "endpoint": endpoint,
                "usage_history": usage_history.get(endpoint_id, []),
                "history_source": "server",
                "mode": "health_fallback",
                "read_only": True,
                "warnings": [str(exc), "Showing health-only monitoring for this endpoint."],
            }

    def update_endpoint(self, endpoint_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = self.runpod.update_endpoint(endpoint_id, patch)
        except Exception as exc:
            if _is_admin_api_permission_error(exc):
                raise RuntimeError(
                    "This RunPod key can submit jobs and report health, but RunPod denied endpoint management access."
                ) from exc
            raise
        endpoint = normalize_runpod_endpoint(payload, managed_services=self.managed_endpoint_map.get(endpoint_id, []))
        return {"endpoint": endpoint, "applied_patch": patch}

    def get_history(
        self,
        *,
        endpoint_id: str | None = None,
        days: int = 7,
        usage_resolution: str = "auto",
        billing_resolution: str = "auto",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _RUNPOD_HISTORY_SNAPSHOT_LIMIT,
    ) -> dict[str, Any]:
        store = get_runpod_metrics_store(self.output_root)
        endpoint_ids = [endpoint_id] if endpoint_id else [
            str(service.get("endpoint_id") or "").strip()
            for service in self.managed_services
            if str(service.get("endpoint_id") or "").strip()
        ]
        usage_history, usage_resolution_used = store.read_usage_history(
            endpoint_ids=endpoint_ids,
            days=max(int(days), 1),
            resolution=usage_resolution,
            start_time=start_time,
            end_time=end_time,
            limit=max(int(limit), 1),
        )
        billing_history, billing_resolution_used = store.read_billing_history(
            endpoint_ids=endpoint_ids,
            days=max(int(days), 1),
            resolution=billing_resolution,
            start_time=start_time,
            end_time=end_time,
            limit=max(int(limit), 1),
        )
        collector = store.collector_status()
        utc_now = datetime.now(UTC)
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00")) if end_time else utc_now
        start_dt = (
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if start_time
            else end_dt - timedelta(days=max(int(days), 1))
        )
        return {
            "usage_history": usage_history,
            "billing_history": billing_history,
            "history_source": "sqlite",
            "collector": collector,
            "window": {
                "start_time": start_dt.isoformat().replace("+00:00", "Z"),
                "end_time": end_dt.isoformat().replace("+00:00", "Z"),
                "days": max(int(days), 1),
                "endpoint_id": endpoint_id or "",
                "usage_resolution": usage_resolution_used,
                "billing_resolution": billing_resolution_used,
            },
        }

    def list_billing(
        self,
        *,
        endpoint_id: str | None = None,
        days: int = 7,
        bucket_size: str = "day",
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        utc_now = datetime.now(UTC)
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00")) if end_time else utc_now
        start_dt = (
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if start_time
            else end_dt - timedelta(days=max(int(days), 1))
        )
        try:
            payload = self.runpod.list_endpoint_billing(
                start_time=start_dt.isoformat().replace("+00:00", "Z"),
                end_time=end_dt.isoformat().replace("+00:00", "Z"),
                bucket_size=bucket_size,
                endpoint_id=endpoint_id,
            )
            records = [
                _normalize_billing_record(item, self.managed_endpoint_map)
                for item in _extract_billing_items(payload)
            ]
            records.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item.get("endpoint_id") or "")))
            return {
                "records": records,
                "summary": _summarize_billing(records, self.managed_endpoint_map),
                "window": {
                    "start_time": start_dt.isoformat().replace("+00:00", "Z"),
                    "end_time": end_dt.isoformat().replace("+00:00", "Z"),
                    "bucket_size": bucket_size,
                    "endpoint_id": endpoint_id or "",
                },
                "mode": "rest",
                "read_only": False,
            }
        except Exception as exc:
            if not _is_admin_api_permission_error(exc):
                raise
            records: list[dict[str, Any]] = []
            return {
                "records": records,
                "summary": _summarize_billing(records, self.managed_endpoint_map),
                "window": {
                    "start_time": start_dt.isoformat().replace("+00:00", "Z"),
                    "end_time": end_dt.isoformat().replace("+00:00", "Z"),
                    "bucket_size": bucket_size,
                    "endpoint_id": endpoint_id or "",
                },
                "mode": "unavailable",
                "read_only": True,
                "warnings": [str(exc), "Billing history requires RunPod admin API access."],
            }


def build_runpod_admin_service(runner: PipelineRunner) -> RunPodAdminService | None:
    runpod = _extract_runpod_client(runner)
    if runpod is None:
        return None
    managed_endpoint_map, managed_services = collect_managed_endpoint_refs(runner)
    get_runpod_metrics_store(runner.output_root)
    return RunPodAdminService(
        runpod=runpod,
        output_root=runner.output_root,
        managed_endpoint_map=managed_endpoint_map,
        managed_services=managed_services,
    )
