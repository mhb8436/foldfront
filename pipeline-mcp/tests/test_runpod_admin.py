import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pipeline_mcp.clients.runpod import RunPodClient
from pipeline_mcp.pipeline import PipelineRunner
from pipeline_mcp.runpod_admin import _history_sample_from_endpoint
from pipeline_mcp.runpod_admin import sanitize_runpod_endpoint_patch
from pipeline_mcp.tools import ToolDispatcher


class FakeRunPod(RunPodClient):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.updated: list[tuple[str, dict[str, object]]] = []

    def list_endpoints(self, *, include_workers: bool = False, include_template: bool = True):  # type: ignore[override]
        worker_payload = [
            {
                "id": "worker-1",
                "desiredStatus": "RUNNING",
                "gpuTypeIds": ["NVIDIA A100 80GB PCIe"],
                "costPerHr": 1.23,
            }
        ]
        return {
            "data": [
                {
                    "id": "ep-managed",
                    "name": "MMseqs Production",
                    "computeType": "GPU",
                    "gpuTypeIds": ["NVIDIA A100 80GB PCIe"],
                    "dataCenterIds": ["US-KS-2"],
                    "workersMin": 1,
                    "workersMax": 4,
                    "scalerType": "QUEUE_DELAY",
                    "scalerValue": 3,
                    "idleTimeout": 5,
                    "executionTimeoutMs": 600000,
                    "flashBoot": True,
                    "template": {"id": "tmpl-1", "name": "mmseqs-runpod", "imageName": "kbf/mmseqs:latest"},
                    "workers": worker_payload if include_workers else [],
                },
                {
                    "id": "ep-other",
                    "name": "Research Sandbox",
                    "computeType": "GPU",
                    "gpuTypeIds": ["NVIDIA L40S"],
                    "workersMin": 0,
                    "workersMax": 2,
                    "scalerType": "REQUEST_COUNT",
                    "scalerValue": 1,
                    "idleTimeout": 10,
                    "executionTimeoutMs": 900000,
                    "flashBoot": False,
                    "template": {"id": "tmpl-2", "name": "sandbox", "imageName": "kbf/sandbox:latest"},
                    "workers": [],
                },
            ]
        }

    def get_endpoint(self, endpoint_id: str, *, include_workers: bool = True, include_template: bool = True):  # type: ignore[override]
        endpoints = self.list_endpoints(include_workers=include_workers, include_template=include_template)["data"]
        for item in endpoints:
            if item["id"] == endpoint_id:
                return item
        raise AssertionError(f"Unknown endpoint_id in test: {endpoint_id}")

    def update_endpoint(self, endpoint_id: str, patch: dict[str, object]):  # type: ignore[override]
        self.updated.append((endpoint_id, patch))
        payload = dict(self.get_endpoint(endpoint_id))
        payload.update(patch)
        return payload

    def list_endpoint_billing(self, *, start_time: str, end_time: str, bucket_size: str = "day", endpoint_id: str | None = None):  # type: ignore[override]
        return {
            "billingHistory": [
                {
                    "endpointId": "ep-managed",
                    "bucketStart": "2026-03-09T00:00:00Z",
                    "cost": 12.5,
                    "gpuTypeId": "NVIDIA A100 80GB PCIe",
                },
                {
                    "endpointId": "ep-other",
                    "bucketStart": "2026-03-09T00:00:00Z",
                    "cost": 2.0,
                    "gpuTypeId": "NVIDIA L40S",
                },
            ]
        }


class DeniedAdminRunPod(FakeRunPod):
    def list_endpoints(self, *, include_workers: bool = False, include_template: bool = True):  # type: ignore[override]
        raise RuntimeError("RUNPOD_API_KEY was rejected by the RunPod API. Update the key and restart pipeline-mcp.")

    def get_endpoint(self, endpoint_id: str, *, include_workers: bool = True, include_template: bool = True):  # type: ignore[override]
        raise RuntimeError("RUNPOD_API_KEY was rejected by the RunPod API. Update the key and restart pipeline-mcp.")

    def update_endpoint(self, endpoint_id: str, patch: dict[str, object]):  # type: ignore[override]
        raise RuntimeError("RUNPOD_API_KEY was rejected by the RunPod API. Update the key and restart pipeline-mcp.")

    def list_endpoint_billing(self, *, start_time: str, end_time: str, bucket_size: str = "day", endpoint_id: str | None = None):  # type: ignore[override]
        raise RuntimeError("RUNPOD_API_KEY was rejected by the RunPod API. Update the key and restart pipeline-mcp.")

    def health(self, endpoint_id: str):  # type: ignore[override]
        return {
            "jobs": {"completed": 10, "failed": 1, "inProgress": 2, "inQueue": 3, "retried": 4},
            "workers": {"idle": 1, "ready": 2, "running": 1},
        }


class SequencedHealthRunPod(DeniedAdminRunPod):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def health(self, endpoint_id: str):  # type: ignore[override]
        self.calls += 1
        if endpoint_id == "ep-managed":
            total = 1 if self.calls <= 2 else 3
            queued = 0 if self.calls <= 2 else 2
            running = 1 if self.calls <= 2 else 2
            return {
                "jobs": {"completed": 5, "failed": 0, "inProgress": running, "inQueue": queued, "retried": 0},
                "workers": {"running": total},
            }
        return {
            "jobs": {"completed": 0, "failed": 0, "inProgress": 0, "inQueue": 0, "retried": 0},
            "workers": {"idle": 1},
        }


class TestRunPodAdmin(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="runpod-admin-tests-")
        self.output_root = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _dispatcher(self, runpod: FakeRunPod | None = None) -> tuple[ToolDispatcher, FakeRunPod]:
        fake_runpod = runpod or FakeRunPod()
        runner = PipelineRunner(
            output_root=self.output_root,
            mmseqs=SimpleNamespace(runpod=fake_runpod, endpoint_id="ep-managed"),
            proteinmpnn=SimpleNamespace(runpod=fake_runpod, endpoint_id="ep-missing"),
        )
        return ToolDispatcher(runner), fake_runpod

    def test_sanitize_runpod_endpoint_patch_accepts_scaling_fields(self) -> None:
        patch = sanitize_runpod_endpoint_patch(
            {
                "gpuTypeIds": ["NVIDIA A100 80GB PCIe"],
                "workersMin": 0,
                "workersMax": 3,
                "scalerType": "QUEUE_DELAY",
                "scalerValue": 2,
                "idleTimeout": 5,
                "executionTimeoutMs": 600000,
                "flashBoot": True,
            }
        )
        self.assertEqual(patch["workersMax"], 3)
        self.assertTrue(bool(patch["flashBoot"]))

    def test_sanitize_runpod_endpoint_patch_rejects_invalid_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "workersMin"):
            sanitize_runpod_endpoint_patch({"workersMin": 4, "workersMax": 1})

    def test_dispatcher_lists_managed_and_missing_endpoints(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher()
        result = dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        endpoints = result.get("endpoints") or []
        missing = result.get("missing_endpoints") or []
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["endpoint_id"], "ep-missing")
        self.assertEqual(result["summary"]["managed_endpoints"], 1)

    def test_dispatcher_gets_endpoint_detail(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher()
        result = dispatcher.call_tool("pipeline.runpod_get_endpoint", {"endpoint_id": "ep-managed"})
        endpoint = result["endpoint"]
        self.assertEqual(endpoint["id"], "ep-managed")
        self.assertEqual(endpoint["worker_summary"]["total"], 1)

    def test_dispatcher_includes_server_usage_history(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(DeniedAdminRunPod())
        result = dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        history = result.get("usage_history") or {}
        self.assertEqual(result.get("history_source"), "server")
        self.assertIn("ep-managed", history)
        self.assertEqual(history["ep-managed"][-1]["workers"], 4)
        self.assertEqual(history["ep-managed"][-1]["queued"], 3)

    def test_dispatcher_persists_usage_history_across_calls(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(SequencedHealthRunPod())
        dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        result = dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        history = result.get("usage_history") or {}
        self.assertGreaterEqual(len(history.get("ep-managed") or []), 2)
        self.assertEqual(history["ep-managed"][-1]["workers"], 3)
        history_path = Path(self.output_root) / "_runpod_admin" / "metrics.sqlite"
        self.assertTrue(history_path.exists())

    def test_history_sample_from_endpoint_does_not_treat_warm_workers_as_running(self) -> None:
        sample = _history_sample_from_endpoint(
            {
                "id": "ep-managed",
                "worker_summary": {"total": 4, "states": {"ready": 2, "warm": 2}},
                "health_jobs": {"in_queue": 0, "in_progress": 0, "completed": 0, "failed": 0, "retried": 0},
            },
            captured_at="2026-03-11T00:00:00Z",
            mode="rest",
        )

        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample["workers"], 4)
        self.assertEqual(sample["running"], 0)

    def test_dispatcher_updates_endpoint(self) -> None:
        dispatcher, fake_runpod = self._dispatcher()
        result = dispatcher.call_tool(
            "pipeline.runpod_update_endpoint",
            {
                "endpoint_id": "ep-managed",
                "patch": {"workersMin": 0, "workersMax": 0},
            },
        )
        self.assertEqual(fake_runpod.updated[0][0], "ep-managed")
        self.assertEqual(result["endpoint"]["workers_max"], 0)

    def test_dispatcher_reads_server_history_tool(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(SequencedHealthRunPod())
        dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        history = dispatcher.call_tool("pipeline.runpod_get_history", {"days": 7})
        self.assertEqual(history.get("history_source"), "sqlite")
        self.assertIn("ep-managed", history.get("usage_history") or {})
        self.assertEqual(history["window"]["days"], 7)
        self.assertTrue(bool(history.get("collector")))

    def test_dispatcher_lists_billing(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher()
        result = dispatcher.call_tool("pipeline.runpod_list_billing", {"days": 7})
        self.assertEqual(result["summary"]["records"], 2)
        self.assertAlmostEqual(result["summary"]["total_cost"], 14.5)

    def test_dispatcher_falls_back_to_health_when_admin_api_is_denied(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(DeniedAdminRunPod())
        result = dispatcher.call_tool("pipeline.runpod_list_endpoints", {})
        endpoints = result.get("endpoints") or []
        self.assertEqual(result.get("mode"), "health_fallback")
        self.assertTrue(bool(result.get("read_only")))
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(endpoints[0]["worker_summary"]["total"], 4)
        self.assertEqual(endpoints[0]["health_jobs"]["in_queue"], 3)

    def test_dispatcher_returns_unavailable_billing_when_admin_api_is_denied(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(DeniedAdminRunPod())
        result = dispatcher.call_tool("pipeline.runpod_list_billing", {"days": 7})
        self.assertEqual(result.get("mode"), "unavailable")
        self.assertEqual(result["summary"]["records"], 0)

    def test_dispatcher_rejects_update_when_admin_api_is_denied(self) -> None:
        dispatcher, _fake_runpod = self._dispatcher(DeniedAdminRunPod())
        with self.assertRaisesRegex(RuntimeError, "cannot submit jobs and report health|RunPod key can submit jobs and report health"):
            dispatcher.call_tool(
                "pipeline.runpod_update_endpoint",
                {
                    "endpoint_id": "ep-managed",
                    "patch": {"workersMin": 0, "workersMax": 0},
                },
            )


if __name__ == "__main__":
    unittest.main()
