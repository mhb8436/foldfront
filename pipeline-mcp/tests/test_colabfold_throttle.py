"""Tests for the process-wide ColabFold concurrency gate in clients.local_http.

The gate caps how many AF2/ColabFold predict() HTTP calls run at once,
regardless of how callers parallelize (nested ThreadPoolExecutors etc.),
so the shared gateway's fixed worker pool is never flooded.
"""
from __future__ import annotations

import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import pipeline_mcp.clients.local_http as lh


@pytest.fixture(autouse=True)
def _reset_gate(monkeypatch):
    # Reset the module-level singleton between tests so each test picks up its
    # own COLABFOLD_MAX_CONCURRENCY value.
    lh._COLABFOLD_SEMAPHORE = None
    lh._COLABFOLD_SEMAPHORE_LIMIT = 0
    yield
    lh._COLABFOLD_SEMAPHORE = None
    lh._COLABFOLD_SEMAPHORE_LIMIT = 0


def test_default_concurrency_is_four(monkeypatch):
    monkeypatch.delenv("COLABFOLD_MAX_CONCURRENCY", raising=False)
    assert lh._colabfold_max_concurrency() == 4


def test_env_overrides_concurrency(monkeypatch):
    monkeypatch.setenv("COLABFOLD_MAX_CONCURRENCY", "2")
    assert lh._colabfold_max_concurrency() == 2


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("COLABFOLD_MAX_CONCURRENCY", "not-a-number")
    assert lh._colabfold_max_concurrency() == 4


def test_zero_disables_gate(monkeypatch):
    monkeypatch.setenv("COLABFOLD_MAX_CONCURRENCY", "0")
    assert lh._colabfold_gate() is None


def test_gate_caps_simultaneous_holders(monkeypatch):
    monkeypatch.setenv("COLABFOLD_MAX_CONCURRENCY", "4")

    peak = 0
    current = 0
    lock = threading.Lock()
    start = threading.Event()

    def worker():
        nonlocal peak, current
        start.wait()
        gate = lh._colabfold_gate()
        with (gate if gate is not None else _nullcontext()):
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with lock:
                current -= 1

    # Fan out far more workers than the cap (simulates 3 targets x 4 af2 workers)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(worker) for _ in range(12)]
        start.set()
        for f in futures:
            f.result()

    assert peak <= 4, f"gate allowed {peak} concurrent holders, expected <= 4"


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
