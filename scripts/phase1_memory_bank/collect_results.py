#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pickle
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


def list_completed(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents", []) or []:
            if obj["Key"].endswith("/result.json"):
                keys.append(obj["Key"])
    return sorted(keys)


def download_json(s3, bucket: str, key: str) -> dict:
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def train_expert_model(
    target_id: str,
    rows: list[dict],
    embeddings_csv: Path | None,
) -> dict | None:
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestRegressor
    except ImportError:
        print("sklearn not available; skipping Expert model training", file=sys.stderr)
        return None

    if embeddings_csv is None or not embeddings_csv.exists():
        return None

    import csv

    seq_to_emb: dict[str, list[float]] = {}
    with embeddings_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("seq_id") or row.get("id")
            emb_str = row.get("embedding", "")
            if not sid or not emb_str:
                continue
            try:
                seq_to_emb[sid] = [float(x) for x in emb_str.split(",")]
            except ValueError:
                continue

    X, y = [], []
    for r in rows:
        sid = r["id"]
        if sid in seq_to_emb:
            X.append(seq_to_emb[sid])
            y.append(r["plddt"])

    if len(X) < 10:
        return None

    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    rf = RandomForestRegressor(n_estimators=100, random_state=42)
    rf.fit(X_arr, y_arr)

    return {
        "model": rf,
        "target_pdb": target_id,
        "train_samples": len(X_arr),
        "best_plddt": float(y_arr.max()),
        "timestamp": time.time(),
        "source": "phase1_memory_bank",
    }


def parse_args():
    p = argparse.ArgumentParser(description="Download AF2 results and build Expert memory bank")
    p.add_argument("--env", type=Path, default=Path(__file__).parent / ".env")
    p.add_argument("--output-dir", type=Path, default=Path("/opt/protein_pipeline/pipeline-mcp/models/experts"))
    p.add_argument("--dataset-csv", type=Path, default=Path("/opt/protein_pipeline/phase1_dataset.csv"))
    p.add_argument("--embeddings-csv", type=Path, default=None, help="Optional CSV with seq_id,embedding for RF training")
    p.add_argument("--archive-completed", action="store_true", help="Move completed jobs to archive/ after download")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    load_env(args.env)

    s3 = make_s3_client()
    bucket = os.environ["RUNPOD_S3_BUCKET"]
    completed_prefix = os.environ.get("S3_COMPLETED", "phase1_memory_bank/jobs/completed")

    keys = list_completed(s3, bucket, completed_prefix)
    print(f"Found {len(keys)} completed jobs in s3://{bucket}/{completed_prefix}/")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.dataset_csv.parent.mkdir(parents=True, exist_ok=True)

    target_rows: dict[str, list[dict]] = {}
    all_rows: list[dict] = []

    for key in keys:
        result = download_json(s3, bucket, key)
        target_id = result.get("target_id", "unknown")
        scores = result.get("plddt_scores", {})
        for sid, plddt in scores.items():
            row = {
                "id": sid,
                "target_id": target_id,
                "plddt": float(plddt),
                "job_id": result.get("job_id"),
            }
            target_rows.setdefault(target_id, []).append(row)
            all_rows.append(row)

    print(f"Aggregated {len(all_rows)} pLDDT datapoints across {len(target_rows)} targets")

    import csv
    with args.dataset_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "target_id", "plddt", "job_id"])
        writer.writeheader()
        for r in all_rows:
            writer.writerow(r)
    print(f"Dataset exported to {args.dataset_csv}")

    trained = 0
    for target_id, rows in target_rows.items():
        expert = train_expert_model(target_id, rows, args.embeddings_csv)
        if expert is None:
            continue
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in target_id)
        out_path = args.output_dir / f"expert_{safe_id}_phase1.pkl"
        with out_path.open("wb") as f:
            pickle.dump(expert, f)
        trained += 1

    if trained > 0:
        print(f"Trained and archived {trained} Expert models to {args.output_dir}")
    else:
        print("Skipped Expert model training (provide --embeddings-csv to enable)")

    if args.archive_completed:
        archive_prefix = completed_prefix.replace("/completed", "/archive")
        for key in keys:
            job_dir = key.rsplit("/", 1)[0]
            new_key = job_dir.replace(completed_prefix, archive_prefix) + "/result.json"
            try:
                s3.copy_object(
                    Bucket=bucket,
                    CopySource={"Bucket": bucket, "Key": key},
                    Key=new_key,
                )
                s3.delete_object(Bucket=bucket, Key=key)
            except Exception as e:
                print(f"Archive failed for {key}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
