"""Concurrent-user measurement against a running API.

Not a benchmark of the machine: a measurement of whether N people can use the
console at once and each get an answer within the bound. Each virtual user
does what a console tab does - polls the dashboard, the run list, the bell,
opens a run - with a pause between, for the whole duration.

    uv run python tools/load_test.py --users 50 --seconds 30

Reads only. It creates nothing and can be run against a live installation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

#  What one console tab actually requests, roughly in proportion.
PATHS = [
    "/api/v1/summary?recent=6",
    "/api/v1/runs?limit=50",
    "/api/v1/notices",
    "/api/v1/jobs/stats",
    "/api/v1/workflows",
    "/api/v1/projects",
    "/api/v1/me",
]


async def user(client: httpx.AsyncClient, deadline: float, samples: list, errors: list, think: float) -> None:
    #  A run to open, if there is one, so the detail path is measured too
    detail: list[str] = []
    try:
        r = await client.get("/api/v1/runs?limit=1")
        items = r.json().get("items", [])
        if items:
            rid = items[0]["run_id"]
            detail = [f"/api/v1/runs/{rid}", f"/api/v1/runs/{rid}/events"]
    except Exception:
        pass
    paths = PATHS + detail
    i = 0
    while time.monotonic() < deadline:
        path = paths[i % len(paths)]
        i += 1
        t0 = time.perf_counter()
        try:
            r = await client.get(path)
            dt = time.perf_counter() - t0
            if r.status_code >= 400:
                errors.append((path, r.status_code))
            else:
                samples.append((path, dt))
        except Exception as exc:  # noqa: BLE001
            errors.append((path, type(exc).__name__))
        await asyncio.sleep(think)


async def main(base: str, users: int, seconds: int, think: float, bound: float) -> int:
    samples: list[tuple[str, float]] = []
    errors: list[tuple[str, object]] = []
    limits = httpx.Limits(max_connections=users + 5, max_keepalive_connections=users + 5)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        deadline = time.monotonic() + seconds
        t0 = time.monotonic()
        await asyncio.gather(*(user(client, deadline, samples, errors, think) for _ in range(users)))
        elapsed = time.monotonic() - t0

    lat = sorted(dt for _, dt in samples)
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))] if lat else float("nan")
    by_path: dict[str, list[float]] = {}
    for path, dt in samples:
        by_path.setdefault(path.split("?")[0].replace("/api/v1", ""), []).append(dt)

    report = {
        "users": users, "seconds": seconds, "think_seconds": think,
        "requests": len(samples), "errors": len(errors),
        "req_per_s": round(len(samples) / elapsed, 1),
        "latency_s": {"p50": round(q(0.5), 4), "p95": round(q(0.95), 4),
                      "p99": round(q(0.99), 4), "max": round(max(lat), 4) if lat else None},
        "within_bound": {"bound_s": bound,
                          "share": round(sum(1 for d in lat if d <= bound) / len(lat), 4) if lat else None},
        "by_path": {p: {"n": len(v), "p95": round(sorted(v)[int(len(v) * 0.95) if len(v) > 1 else 0], 4)}
                    for p, v in sorted(by_path.items())},
        "error_sample": errors[:5],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = not errors and lat and q(0.95) <= bound
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18090")
    ap.add_argument("--users", type=int, default=50)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--think", type=float, default=0.5, help="pause between a user's requests")
    ap.add_argument("--bound", type=float, default=3.0, help="response bound in seconds")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.base, a.users, a.seconds, a.think, a.bound)))
