import tempfile
import unittest
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from pipeline_mcp.runpod_metrics import RunPodMetricsStore
from pipeline_mcp.runpod_metrics import _normalize_health_sample


class TestRunPodMetricsStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="runpod-metrics-")
        self.db_path = Path(self._tmpdir.name) / "metrics.sqlite"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_prune_old_data_keeps_recent_rows_only(self) -> None:
        store = RunPodMetricsStore(
            db_path=self.db_path,
            usage_retention_days=100,
            billing_retention_days=100,
        )
        now = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
        store.record_usage_samples(
            [
                {
                    "endpoint_id": "ep-1",
                    "t": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
                    "workers": 1,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": now.isoformat().replace("+00:00", "Z"),
                    "workers": 2,
                },
            ]
        )
        store.record_billing_records(
            [
                {
                    "endpoint_id": "ep-1",
                    "t": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
                    "cost": 1.0,
                    "records": 1,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": now.isoformat().replace("+00:00", "Z"),
                    "cost": 2.0,
                    "records": 1,
                },
            ]
        )

        test_store = RunPodMetricsStore(
            db_path=self.db_path,
            usage_retention_days=2,
            billing_retention_days=2,
        )
        test_store.prune_old_data(now=now)

        usage, _resolution = store.read_usage_history(
            end_time=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            days=7,
            resolution="raw",
        )
        billing, _resolution = store.read_billing_history(
            end_time=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            days=7,
            resolution="day",
        )
        self.assertEqual(len(usage["ep-1"]), 1)
        self.assertEqual(usage["ep-1"][0]["workers"], 2)
        self.assertEqual(len(billing["ep-1"]), 1)
        self.assertAlmostEqual(billing["ep-1"][0]["cost"], 2.0)

    def test_read_usage_history_supports_daily_rollup(self) -> None:
        store = RunPodMetricsStore(db_path=self.db_path)
        store.record_usage_samples(
            [
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-03-09T01:00:00Z",
                    "workers": 1,
                    "queued": 0,
                    "running": 1,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-03-09T10:00:00Z",
                    "workers": 3,
                    "queued": 2,
                    "running": 2,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-03-10T02:00:00Z",
                    "workers": 2,
                    "queued": 1,
                    "running": 1,
                },
            ]
        )
        history, resolution = store.read_usage_history(
            end_time="2026-03-10T23:59:59Z", days=7, resolution="day"
        )
        self.assertEqual(resolution, "day")
        self.assertEqual(len(history["ep-1"]), 2)
        self.assertEqual(history["ep-1"][0]["workers"], 3)
        self.assertEqual(history["ep-1"][0]["queued"], 2)

    def test_read_billing_history_supports_monthly_rollup(self) -> None:
        store = RunPodMetricsStore(db_path=self.db_path)
        store.record_billing_records(
            [
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-01-05T00:00:00Z",
                    "cost": 1.5,
                    "records": 1,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-01-21T00:00:00Z",
                    "cost": 2.0,
                    "records": 1,
                },
                {
                    "endpoint_id": "ep-1",
                    "t": "2026-02-03T00:00:00Z",
                    "cost": 4.0,
                    "records": 1,
                },
            ]
        )

        history, resolution = store.read_billing_history(
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-28T23:59:59Z",
            resolution="month",
        )

        self.assertEqual(resolution, "month")
        self.assertEqual(
            history["ep-1"],
            [
                {
                    "t": "2026-01-01T00:00:00Z",
                    "cost": 3.5,
                    "records": 2,
                    "mode": "rest",
                },
                {
                    "t": "2026-02-01T00:00:00Z",
                    "cost": 4.0,
                    "records": 1,
                    "mode": "rest",
                },
            ],
        )

    def test_normalize_health_sample_does_not_promote_warm_workers_to_running(
        self,
    ) -> None:
        sample = _normalize_health_sample(
            "ep-1",
            {
                "jobs": {"completed": 0, "failed": 0, "inQueue": 0, "retried": 0},
                "workers": {"idle": 2, "ready": 1, "warm": 3},
            },
            source_mode="collector",
        )

        self.assertEqual(sample["workers"], 6)
        self.assertEqual(sample["queued"], 0)
        self.assertEqual(sample["running"], 0)
