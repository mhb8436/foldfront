#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.client import Config


def load_env(env_file: Path) -> None:
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["RUNPOD_S3_ENDPOINT"],
        aws_access_key_id=os.environ["RUNPOD_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["RUNPOD_S3_SECRET_KEY"],
        region_name=os.environ["RUNPOD_S3_REGION"],
        config=Config(signature_version="s3v4"),
    )


def count_prefix(s3, bucket: str, prefix: str, suffix: str = ".json") -> int:
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents", []) or []:
            if obj["Key"].endswith(suffix):
                count += 1
    return count


def list_stale_processing(s3, bucket: str, prefix: str, stale_minutes: int) -> list[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    stale = []
    cutoff = time.time() - stale_minutes * 60
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents", []) or []:
            if not obj["Key"].endswith(".json"):
                continue
            modified_ts = obj["LastModified"].timestamp()
            if modified_ts < cutoff:
                stale.append({
                    "key": obj["Key"],
                    "age_min": (time.time() - modified_ts) / 60,
                })
    return stale


def render_bar(current: int, total: int, width: int = 40) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    filled = int(width * current / total)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def parse_args():
    p = argparse.ArgumentParser(description="Monitor Phase 1 job queue on RunPod S3")
    p.add_argument("--env", type=Path, default=Path(__file__).parent / ".env")
    p.add_argument("--interval", type=int, default=30, help="Refresh interval in seconds")
    p.add_argument("--stale-minutes", type=int, default=20, help="Mark processing jobs stale after N minutes")
    p.add_argument("--once", action="store_true", help="Print once and exit")
    p.add_argument("--recover-stale", action="store_true", help="Move stale processing jobs back to pending")
    return p.parse_args()


def recover_stale(s3, bucket: str, pending_prefix: str, stale: list[dict]) -> int:
    recovered = 0
    for entry in stale:
        key = entry["key"]
        job_name = Path(key).name
        try:
            s3.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": key},
                Key=f"{pending_prefix}/{job_name}",
            )
            s3.delete_object(Bucket=bucket, Key=key)
            recovered += 1
        except Exception as e:
            print(f"Recover failed for {key}: {e}", file=sys.stderr)
    return recovered


def main() -> int:
    args = parse_args()
    load_env(args.env)

    s3 = make_s3_client()
    bucket = os.environ["RUNPOD_S3_BUCKET"]
    pending = os.environ.get("S3_PENDING", "phase1_memory_bank/jobs/pending")
    processing = os.environ.get("S3_PROCESSING", "phase1_memory_bank/jobs/processing")
    completed = os.environ.get("S3_COMPLETED", "phase1_memory_bank/jobs/completed")
    failed = os.environ.get("S3_FAILED", "phase1_memory_bank/jobs/failed")

    last_completed = -1
    last_ts = time.time()

    try:
        while True:
            n_pending = count_prefix(s3, bucket, pending)
            n_processing = count_prefix(s3, bucket, processing)
            n_completed_total = count_prefix(s3, bucket, completed, suffix="/result.json")
            n_failed = count_prefix(s3, bucket, failed)

            total = n_pending + n_processing + n_completed_total + n_failed
            stale = list_stale_processing(s3, bucket, processing, args.stale_minutes)

            now = time.time()
            rate = 0.0
            if last_completed >= 0:
                elapsed = now - last_ts
                delta = n_completed_total - last_completed
                if elapsed > 0:
                    rate = delta / elapsed * 3600
            last_completed = n_completed_total
            last_ts = now

            os.system("clear" if os.name == "posix" else "cls")
            print(f"Phase 1 Memory Bank — Job Queue Monitor ({time.strftime('%H:%M:%S')})")
            print(f"Bucket: s3://{bucket}/")
            print()

            print(f"  Pending    : {n_pending:>6d}  {render_bar(n_pending, total)}")
            print(f"  Processing : {n_processing:>6d}  {render_bar(n_processing, total)}")
            print(f"  Completed  : {n_completed_total:>6d}  {render_bar(n_completed_total, total)}")
            print(f"  Failed     : {n_failed:>6d}")
            print(f"  TOTAL      : {total:>6d}")
            print()
            print(f"  Throughput : {rate:.1f} jobs/hour (last {args.interval}s window)")
            if rate > 0 and n_pending + n_processing > 0:
                eta_hr = (n_pending + n_processing) / rate
                print(f"  ETA        : {eta_hr:.1f} hours ({eta_hr/24:.1f} days)")
            print()

            if stale:
                print(f"  ⚠️  {len(stale)} stale processing jobs (>{args.stale_minutes}min):")
                for entry in stale[:5]:
                    print(f"     - {Path(entry['key']).name} ({entry['age_min']:.1f}min)")
                if args.recover_stale:
                    n = recover_stale(s3, bucket, pending, stale)
                    print(f"  → Recovered {n} stale jobs back to pending/")

            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
