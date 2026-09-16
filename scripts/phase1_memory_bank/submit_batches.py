#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

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


def read_sequences(input_path: Path) -> list[dict]:
    rows: list[dict] = []
    if input_path.suffix.lower() in (".fasta", ".fa"):
        current_id, current_seq = None, []
        for line in input_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(">"):
                if current_id is not None:
                    rows.append({"id": current_id, "sequence": "".join(current_seq), "target_id": current_id})
                current_id = line[1:].split()[0]
                current_seq = []
            elif line:
                current_seq.append(line)
        if current_id is not None:
            rows.append({"id": current_id, "sequence": "".join(current_seq), "target_id": current_id})
        return rows

    import csv

    with input_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("seq_id") or row.get("id")
            seq = row.get("sequence", "").strip()
            if not sid or not seq:
                continue
            rows.append({
                "id": sid,
                "sequence": seq,
                "target_id": row.get("target_id") or row.get("run_id") or sid,
            })
    return rows


def bucket_by_length(seqs: list[dict], bucket_width: int) -> dict[int, list[dict]]:
    buckets: dict[int, list[dict]] = {}
    for s in seqs:
        L = len(s["sequence"])
        b = ((L + bucket_width - 1) // bucket_width) * bucket_width
        buckets.setdefault(b, []).append(s)
    return buckets


def chunk(items: list[dict], size: int) -> Iterable[list[dict]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def submit(
    s3,
    bucket: str,
    pending_prefix: str,
    seqs_by_target: dict[str, list[dict]],
    batch_size: int,
    bucket_width: int,
    msa_mode: Optional[str] = None,
    msa_host_url: Optional[str] = None,
) -> int:
    submitted = 0
    for target_id, rows in seqs_by_target.items():
        buckets = bucket_by_length(rows, bucket_width)
        for pad_length, items in sorted(buckets.items()):
            for part in chunk(items, batch_size):
                job_id = f"{target_id}_pad{pad_length}_{uuid.uuid4().hex[:8]}"
                payload = {
                    "job_id": job_id,
                    "target_id": target_id,
                    "pad_length": pad_length,
                    "sequences": part,
                    "submitted_at": int(time.time()),
                }
                if msa_mode is not None:
                    payload["msa_mode"] = msa_mode
                if msa_host_url is not None:
                    payload["msa_host_url"] = msa_host_url
                key = f"{pending_prefix}/{job_id}.json"
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )
                submitted += 1
    return submitted


def parse_args():
    p = argparse.ArgumentParser(description="Submit length-bucketed AF2 jobs to RunPod S3")
    p.add_argument("input", type=Path, help="FASTA or CSV with sequences (CSV needs columns: seq_id,sequence,target_id)")
    p.add_argument("--env", type=Path, default=Path(__file__).parent / ".env")
    p.add_argument("--batch-size", type=int, default=8, help="Sequences per GPU job (smaller = less memory)")
    p.add_argument("--bucket-width", type=int, default=32, help="Length bucket width in aa")
    p.add_argument(
        "--msa-mode",
        choices=("single_sequence", "mmseqs2_uniref_env", "mmseqs2_uniref"),
        default=None,
        help="Override MSA mode (default: worker's env setting, usually single_sequence)",
    )
    p.add_argument(
        "--msa-host-url",
        default=None,
        help="Custom MMseqs2 server URL (e.g., local MMseqs2 mirror). Ignored for single_sequence.",
    )
    p.add_argument(
        "--hybrid-rescore-from",
        type=Path,
        default=None,
        help="Path to a previous dataset CSV; only re-submit targets whose mean pLDDT < --rescore-threshold with MSA enabled",
    )
    p.add_argument("--rescore-threshold", type=float, default=70.0)
    return p.parse_args()


def filter_low_plddt_targets(
    seqs_by_target: dict[str, list[dict]],
    prev_csv: Path,
    threshold: float,
) -> dict[str, list[dict]]:
    import csv

    scores_by_target: dict[str, list[float]] = {}
    with prev_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = row.get("target_id")
            plddt = row.get("plddt")
            if not tid or not plddt:
                continue
            try:
                scores_by_target.setdefault(tid, []).append(float(plddt))
            except ValueError:
                continue

    keep = set()
    for tid, scores in scores_by_target.items():
        if scores and (sum(scores) / len(scores)) < threshold:
            keep.add(tid)

    filtered = {tid: rows for tid, rows in seqs_by_target.items() if tid in keep}
    print(f"Hybrid rescore mode: keeping {len(filtered)}/{len(seqs_by_target)} targets with mean pLDDT < {threshold}")
    return filtered


def main() -> int:
    args = parse_args()
    load_env(args.env)

    rows = read_sequences(args.input)
    if not rows:
        print(f"No sequences found in {args.input}", file=sys.stderr)
        return 2

    seqs_by_target: dict[str, list[dict]] = {}
    for r in rows:
        seqs_by_target.setdefault(r["target_id"], []).append(r)

    print(f"Loaded {len(rows)} sequences across {len(seqs_by_target)} targets")

    if args.hybrid_rescore_from:
        seqs_by_target = filter_low_plddt_targets(
            seqs_by_target, args.hybrid_rescore_from, args.rescore_threshold
        )
        if not seqs_by_target:
            print("No targets need rescoring.")
            return 0

    s3 = make_s3_client()
    bucket = os.environ["RUNPOD_S3_BUCKET"]
    pending_prefix = os.environ.get("S3_PENDING", "phase1_memory_bank/jobs/pending")

    n = submit(
        s3,
        bucket,
        pending_prefix,
        seqs_by_target,
        batch_size=args.batch_size,
        bucket_width=args.bucket_width,
        msa_mode=args.msa_mode,
        msa_host_url=args.msa_host_url,
    )
    print(f"Submitted {n} batch jobs to s3://{bucket}/{pending_prefix}/")
    if args.msa_mode:
        print(f"  MSA mode override: {args.msa_mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
