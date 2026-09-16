from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import json
import sqlite3
import threading
from time import monotonic
from typing import Any

from .clients.runpod import RunPodClient
from .pipeline import PipelineRunner


_MANAGED_SERVICE_SPECS: tuple[tuple[str, str], ...] = (
    ("mmseqs", "MMSEQS_ENDPOINT_ID"),
    ("proteinmpnn", "PROTEINMPNN_ENDPOINT_ID"),
    ("colabfold", "COLABFOLD_ENDPOINT_ID"),
    ("af2", "ALPHAFOLD2_ENDPOINT_ID"),
    ("rfd3", "RFD3_ENDPOINT_ID"),
    ("bioemu", "BIOEMU_ENDPOINT_ID"),
    ("diffdock", "DIFFDOCK_ENDPOINT_ID"),
)

_DEFAULT_USAGE_INTERVAL_SECONDS = 60
_DEFAULT_BILLING_INTERVAL_SECONDS = 300
_DEFAULT_USAGE_LIMIT = 120
_DEFAULT_BILLING_LIMIT = 120
_DEFAULT_USAGE_RETENTION_DAYS = 45
_DEFAULT_BILLING_RETENTION_DAYS = 400
_BILLING_SYNC_WINDOW_DAYS = 30
_REGISTRY_LOCK = threading.Lock()
_STORES: dict[str, "RunPodMetricsStore"] = {}
_COLLECTORS: dict[str, "RunPodMetricsCollector"] = {}


def _iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _format_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _extract_runpod_client(runner: PipelineRunner) -> RunPodClient | None:
    for attr, _env_var in _MANAGED_SERVICE_SPECS:
        client = getattr(runner, attr, None)
        runpod = getattr(client, "runpod", None)
        if isinstance(runpod, RunPodClient):
            return runpod
    return None


def _endpoint_ids(runner: PipelineRunner) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for attr, _env_var in _MANAGED_SERVICE_SPECS:
        client = getattr(runner, attr, None)
        endpoint_id = str(getattr(client, "endpoint_id", "") or "").strip()
        if endpoint_id and endpoint_id not in seen:
            seen.add(endpoint_id)
            found.append(endpoint_id)
    return found


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


def _permission_error(exc: Exception) -> bool:
    message = str(exc or "")
    return "RUNPOD_API_KEY was rejected" in message or "does not have permission" in message


def _extract_billing_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("billingHistory", "records", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _first_str(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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


def _normalize_billing_records(payload: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _extract_billing_items(payload):
        endpoint_id = _first_str(item, "endpointId", "endpoint_id")
        timestamp = _first_str(item, "bucketStart", "startTime", "date", "timestamp", "time")
        if not endpoint_id or not timestamp:
            continue
        cost = _first_number(item, "cost", "totalCost", "amount", "billingAmount") or 0.0
        rows.append(
            {
                "endpoint_id": endpoint_id,
                "t": timestamp,
                "cost": round(float(cost), 6),
                "records": 1,
            }
        )
    return rows


def _normalize_health_sample(endpoint_id: str, payload: dict[str, Any], *, source_mode: str) -> dict[str, Any]:
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), dict) else {}
    workers = payload.get("workers") if isinstance(payload.get("workers"), dict) else {}
    running = _int_value(jobs.get("inProgress"))
    if not running:
        running = _int_value(workers.get("running"))
    return {
        "endpoint_id": endpoint_id,
        "t": _iso_utc_now(),
        "workers": sum(_int_value(value) for value in workers.values()),
        "queued": _int_value(jobs.get("inQueue")),
        "running": running,
        "completed": _int_value(jobs.get("completed")),
        "failed": _int_value(jobs.get("failed")),
        "retried": _int_value(jobs.get("retried")),
        "mode": source_mode,
    }


def latest_health(store: "RunPodMetricsStore", endpoint_id: str) -> dict[str, Any] | None:
    """Return the most recent normalized health sample for an endpoint, or None.

    Reads the ``health:<endpoint>`` state key persisted by the collector loop.
    Used by the queue-ETA tool to get live queued/running/workers counts.
    """
    raw = store.get_state(f"health:{endpoint_id}", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "endpoint_id": endpoint_id,
        "queued": _int_value(data.get("queued")),
        "running": _int_value(data.get("running")),
        "workers": _int_value(data.get("workers")),
        "t": data.get("t"),
    }


def _auto_usage_resolution(days: int, requested: str) -> str:
    value = str(requested or "auto").strip().lower() or "auto"
    if value != "auto":
        return value
    if days <= 1:
        return "minute_5"
    if days <= 3:
        return "minute_15"
    if days <= 14:
        return "hour"
    if days <= 90:
        return "day"
    if days <= 180:
        return "week"
    return "month"


def _auto_billing_resolution(days: int, requested: str) -> str:
    value = str(requested or "day").strip().lower() or "day"
    if value != "auto":
        return value
    if days <= 60:
        return "day"
    if days <= 180:
        return "week"
    return "month"


def _bucket_start(value: datetime, resolution: str) -> datetime:
    if resolution == "raw":
        return value.astimezone(UTC)
    item = value.astimezone(UTC).replace(second=0, microsecond=0)
    if resolution == "minute_5":
        return item.replace(minute=(item.minute // 5) * 5)
    if resolution == "minute_15":
        return item.replace(minute=(item.minute // 15) * 15)
    if resolution == "hour":
        return item.replace(minute=0)
    if resolution == "day":
        return item.replace(hour=0, minute=0)
    if resolution == "week":
        start = item.replace(hour=0, minute=0) - timedelta(days=item.weekday())
        return start
    if resolution == "month":
        return item.replace(day=1, hour=0, minute=0)
    return item


@dataclass(frozen=True)
class RunPodMetricsStore:
    db_path: Path
    usage_interval_seconds: int = _DEFAULT_USAGE_INTERVAL_SECONDS
    billing_interval_seconds: int = _DEFAULT_BILLING_INTERVAL_SECONDS
    usage_retention_days: int = _DEFAULT_USAGE_RETENTION_DAYS
    billing_retention_days: int = _DEFAULT_BILLING_RETENTION_DAYS

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    endpoint_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    workers INTEGER NOT NULL DEFAULT 0,
                    queued INTEGER NOT NULL DEFAULT 0,
                    running INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    retried INTEGER NOT NULL DEFAULT 0,
                    source_mode TEXT NOT NULL DEFAULT 'rest',
                    PRIMARY KEY (endpoint_id, captured_at)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_snapshots_time ON usage_snapshots (captured_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS billing_buckets (
                    endpoint_id TEXT NOT NULL,
                    bucket_start TEXT NOT NULL,
                    bucket_size TEXT NOT NULL DEFAULT 'day',
                    cost REAL NOT NULL DEFAULT 0,
                    records INTEGER NOT NULL DEFAULT 0,
                    source_mode TEXT NOT NULL DEFAULT 'rest',
                    PRIMARY KEY (endpoint_id, bucket_start, bucket_size)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_buckets_time ON billing_buckets (bucket_start)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO collector_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value or "")),
            )

    def get_state(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM collector_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row and row["value"] is not None else default

    def collector_status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "usage_interval_seconds": self.usage_interval_seconds,
            "billing_interval_seconds": self.billing_interval_seconds,
            "usage_retention_days": self.usage_retention_days,
            "billing_retention_days": self.billing_retention_days,
            "last_usage_sync": self.get_state("last_usage_sync", ""),
            "last_billing_sync": self.get_state("last_billing_sync", ""),
            "billing_mode": self.get_state("billing_mode", "unknown"),
            "billing_error": self.get_state("billing_error", ""),
        }

    def prune_old_data(self, *, now: datetime | None = None) -> None:
        current = now.astimezone(UTC) if isinstance(now, datetime) else datetime.now(UTC)
        usage_cutoff = _format_iso(current - timedelta(days=max(int(self.usage_retention_days), 1)))
        billing_cutoff = _format_iso(current - timedelta(days=max(int(self.billing_retention_days), 1)))
        with self._connect() as conn:
            conn.execute("DELETE FROM usage_snapshots WHERE captured_at < ?", (usage_cutoff,))
            conn.execute("DELETE FROM billing_buckets WHERE bucket_start < ?", (billing_cutoff,))

    def record_usage_samples(self, samples: list[dict[str, Any]]) -> None:
        rows = [
            (
                str(item.get("endpoint_id") or "").strip(),
                str(item.get("t") or item.get("captured_at") or "").strip(),
                int(item.get("workers") or 0),
                int(item.get("queued") or 0),
                int(item.get("running") or 0),
                int(item.get("completed") or 0),
                int(item.get("failed") or 0),
                int(item.get("retried") or 0),
                str(item.get("mode") or item.get("source_mode") or "rest"),
            )
            for item in samples
            if str(item.get("endpoint_id") or "").strip() and str(item.get("t") or item.get("captured_at") or "").strip()
        ]
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO usage_snapshots(
                    endpoint_id, captured_at, workers, queued, running, completed, failed, retried, source_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint_id, captured_at) DO UPDATE SET
                    workers = excluded.workers,
                    queued = excluded.queued,
                    running = excluded.running,
                    completed = excluded.completed,
                    failed = excluded.failed,
                    retried = excluded.retried,
                    source_mode = excluded.source_mode
                """,
                rows,
            )
        self.prune_old_data()

    def record_billing_records(self, records: list[dict[str, Any]], *, bucket_size: str = "day", source_mode: str = "rest") -> None:
        aggregated: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in records:
            endpoint_id = str(record.get("endpoint_id") or "").strip()
            bucket_start = str(record.get("t") or record.get("bucket_start") or record.get("timestamp") or "").strip()
            if not endpoint_id or not bucket_start:
                continue
            key = (endpoint_id, bucket_start, bucket_size)
            bucket = aggregated.setdefault(key, {"cost": 0.0, "records": 0})
            bucket["cost"] = round(float(bucket["cost"]) + float(record.get("cost") or 0.0), 6)
            bucket["records"] = int(bucket["records"]) + int(record.get("records") or 1)
        if not aggregated:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO billing_buckets(endpoint_id, bucket_start, bucket_size, cost, records, source_mode)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint_id, bucket_start, bucket_size) DO UPDATE SET
                    cost = excluded.cost,
                    records = excluded.records,
                    source_mode = excluded.source_mode
                """,
                [
                    (endpoint_id, bucket_start, size, value["cost"], value["records"], source_mode)
                    for (endpoint_id, bucket_start, size), value in aggregated.items()
                ],
            )
        self.prune_old_data()

    def read_usage_history(
        self,
        *,
        endpoint_ids: list[str] | None = None,
        days: int = 7,
        resolution: str = "auto",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _DEFAULT_USAGE_LIMIT,
    ) -> tuple[dict[str, list[dict[str, Any]]], str]:
        end_dt = _parse_iso(end_time) or datetime.now(UTC)
        start_dt = _parse_iso(start_time) or (end_dt - timedelta(days=max(int(days), 1)))
        actual_resolution = _auto_usage_resolution(max(int((end_dt - start_dt).total_seconds() // 86400), 1), resolution)
        filters = ["captured_at >= ?", "captured_at <= ?"]
        params: list[Any] = [_format_iso(start_dt), _format_iso(end_dt)]
        ids = [str(item).strip() for item in (endpoint_ids or []) if str(item).strip()]
        if ids:
            filters.append(f"endpoint_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        query = f"""
            SELECT endpoint_id, captured_at, workers, queued, running, completed, failed, retried, source_mode
            FROM usage_snapshots
            WHERE {' AND '.join(filters)}
            ORDER BY endpoint_id ASC, captured_at ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            captured_at = _parse_iso(str(row["captured_at"]))
            if captured_at is None:
                continue
            bucket_key = _format_iso(_bucket_start(captured_at, actual_resolution))
            endpoint_id = str(row["endpoint_id"])
            bucket = grouped[endpoint_id].setdefault(
                bucket_key,
                {
                    "t": bucket_key,
                    "workers": 0,
                    "queued": 0,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "retried": 0,
                    "mode": str(row["source_mode"] or "rest"),
                },
            )
            bucket["workers"] = max(int(bucket["workers"]), int(row["workers"] or 0))
            bucket["queued"] = max(int(bucket["queued"]), int(row["queued"] or 0))
            bucket["running"] = max(int(bucket["running"]), int(row["running"] or 0))
            bucket["completed"] = max(int(bucket["completed"]), int(row["completed"] or 0))
            bucket["failed"] = max(int(bucket["failed"]), int(row["failed"] or 0))
            bucket["retried"] = max(int(bucket["retried"]), int(row["retried"] or 0))
        history = {
            endpoint_id: sorted(buckets.values(), key=lambda item: str(item.get("t") or ""))[-max(int(limit), 1) :]
            for endpoint_id, buckets in grouped.items()
        }
        return history, actual_resolution

    def read_billing_history(
        self,
        *,
        endpoint_ids: list[str] | None = None,
        days: int = 30,
        resolution: str = "auto",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _DEFAULT_BILLING_LIMIT,
    ) -> tuple[dict[str, list[dict[str, Any]]], str]:
        end_dt = _parse_iso(end_time) or datetime.now(UTC)
        start_dt = _parse_iso(start_time) or (end_dt - timedelta(days=max(int(days), 1)))
        actual_resolution = _auto_billing_resolution(max(int((end_dt - start_dt).total_seconds() // 86400), 1), resolution)
        filters = ["bucket_start >= ?", "bucket_start <= ?"]
        params: list[Any] = [_format_iso(start_dt), _format_iso(end_dt)]
        ids = [str(item).strip() for item in (endpoint_ids or []) if str(item).strip()]
        if ids:
            filters.append(f"endpoint_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        query = f"""
            SELECT endpoint_id, bucket_start, cost, records, source_mode
            FROM billing_buckets
            WHERE {' AND '.join(filters)}
            ORDER BY endpoint_id ASC, bucket_start ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            bucket_start = _parse_iso(str(row["bucket_start"]))
            if bucket_start is None:
                continue
            bucket_key = _format_iso(_bucket_start(bucket_start, actual_resolution))
            endpoint_id = str(row["endpoint_id"])
            bucket = grouped[endpoint_id].setdefault(
                bucket_key,
                {"t": bucket_key, "cost": 0.0, "records": 0, "mode": str(row["source_mode"] or "rest")},
            )
            bucket["cost"] = round(float(bucket["cost"]) + float(row["cost"] or 0.0), 6)
            bucket["records"] = int(bucket["records"]) + int(row["records"] or 0)
        history = {
            endpoint_id: sorted(buckets.values(), key=lambda item: str(item.get("t") or ""))[-max(int(limit), 1) :]
            for endpoint_id, buckets in grouped.items()
        }
        return history, actual_resolution


class RunPodMetricsCollector(threading.Thread):
    def __init__(
        self,
        *,
        runner: PipelineRunner,
        store: RunPodMetricsStore,
        usage_interval_seconds: int = _DEFAULT_USAGE_INTERVAL_SECONDS,
        billing_interval_seconds: int = _DEFAULT_BILLING_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(daemon=True, name=f"runpod-metrics-{Path(store.db_path).stem}")
        self.runner = runner
        self.store = store
        self.usage_interval_seconds = max(int(usage_interval_seconds), 15)
        self.billing_interval_seconds = max(int(billing_interval_seconds), 60)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.store.set_state("collector_started_at", _iso_utc_now())
        next_usage = 0.0
        next_billing = 0.0
        while not self._stop.is_set():
            now = monotonic()
            if now >= next_usage:
                self._collect_usage()
                next_usage = now + self.usage_interval_seconds
            if now >= next_billing:
                self._collect_billing()
                next_billing = now + self.billing_interval_seconds
            self._stop.wait(5)

    def _collect_usage(self) -> None:
        runpod = _extract_runpod_client(self.runner)
        endpoint_ids = _endpoint_ids(self.runner)
        if runpod is None or not endpoint_ids:
            return
        samples: list[dict[str, Any]] = []
        for endpoint_id in endpoint_ids:
            try:
                payload = runpod.health(endpoint_id)
                if isinstance(payload, dict):
                    sample = _normalize_health_sample(endpoint_id, payload, source_mode="collector")
                    samples.append(sample)
                    self.store.set_state(f"health:{endpoint_id}", json.dumps(sample))
            except Exception as exc:
                self.store.set_state("usage_error", str(exc))
        if samples:
            self.store.record_usage_samples(samples)
            self.store.set_state("last_usage_sync", _iso_utc_now())
            self.store.set_state("usage_error", "")

    def _collect_billing(self) -> None:
        runpod = _extract_runpod_client(self.runner)
        if runpod is None:
            return
        end_dt = datetime.now(UTC)
        start_dt = end_dt - timedelta(days=_BILLING_SYNC_WINDOW_DAYS)
        try:
            payload = runpod.list_endpoint_billing(
                start_time=_format_iso(start_dt),
                end_time=_format_iso(end_dt),
                bucket_size="day",
                endpoint_id=None,
            )
            rows = _normalize_billing_records(payload)
            if rows:
                self.store.record_billing_records(rows, bucket_size="day", source_mode="collector")
            self.store.set_state("last_billing_sync", _iso_utc_now())
            self.store.set_state("billing_mode", "rest")
            self.store.set_state("billing_error", "")
        except Exception as exc:
            self.store.set_state("billing_mode", "unavailable" if _permission_error(exc) else "error")
            self.store.set_state("billing_error", str(exc))


def get_runpod_metrics_store(
    output_root: str,
    *,
    usage_interval_seconds: int = _DEFAULT_USAGE_INTERVAL_SECONDS,
    billing_interval_seconds: int = _DEFAULT_BILLING_INTERVAL_SECONDS,
) -> RunPodMetricsStore:
    db_path = Path(output_root).resolve() / "_runpod_admin" / "metrics.sqlite"
    key = str(db_path)
    with _REGISTRY_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = RunPodMetricsStore(
                db_path=db_path,
                usage_interval_seconds=usage_interval_seconds,
                billing_interval_seconds=billing_interval_seconds,
            )
            _STORES[key] = store
        return store


def ensure_runpod_metrics_collector(
    runner: PipelineRunner,
    *,
    usage_interval_seconds: int = _DEFAULT_USAGE_INTERVAL_SECONDS,
    billing_interval_seconds: int = _DEFAULT_BILLING_INTERVAL_SECONDS,
) -> RunPodMetricsStore:
    store = get_runpod_metrics_store(
        runner.output_root,
        usage_interval_seconds=usage_interval_seconds,
        billing_interval_seconds=billing_interval_seconds,
    )
    runpod = _extract_runpod_client(runner)
    if runpod is None or not _endpoint_ids(runner):
        return store
    key = str(store.db_path)
    with _REGISTRY_LOCK:
        collector = _COLLECTORS.get(key)
        if collector is None or not collector.is_alive():
            store.set_state("collector_started_at", _iso_utc_now())
            collector = RunPodMetricsCollector(
                runner=runner,
                store=store,
                usage_interval_seconds=usage_interval_seconds,
                billing_interval_seconds=billing_interval_seconds,
            )
            _COLLECTORS[key] = collector
            collector.start()
    return store
