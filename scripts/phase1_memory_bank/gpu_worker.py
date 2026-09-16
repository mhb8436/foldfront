#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from botocore.client import Config


@dataclass
class WorkerConfig:
    endpoint_url: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    pending_prefix: str
    processing_prefix: str
    completed_prefix: str
    failed_prefix: str
    workspace: Path
    af2_weights: Path
    num_models: int
    num_recycles: int
    msa_mode: str
    poll_interval: int
    max_idle_minutes: int
    gpu_id: int
    worker_id: str


def load_config(env_file: Optional[Path] = None) -> WorkerConfig:
    if env_file and env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    def req(key: str) -> str:
        v = os.getenv(key)
        if not v:
            raise RuntimeError(f"Missing required env var: {key}")
        return v

    gpu_id = int(os.getenv("WORKER_GPU_ID", "0"))
    worker_id = os.getenv("WORKER_ID", f"gpu{gpu_id}-{os.getpid()}")

    return WorkerConfig(
        endpoint_url=req("RUNPOD_S3_ENDPOINT"),
        region=req("RUNPOD_S3_REGION"),
        bucket=req("RUNPOD_S3_BUCKET"),
        access_key=req("RUNPOD_S3_ACCESS_KEY"),
        secret_key=req("RUNPOD_S3_SECRET_KEY"),
        pending_prefix=os.getenv("S3_PENDING", "phase1_memory_bank/jobs/pending"),
        processing_prefix=os.getenv("S3_PROCESSING", "phase1_memory_bank/jobs/processing"),
        completed_prefix=os.getenv("S3_COMPLETED", "phase1_memory_bank/jobs/completed"),
        failed_prefix=os.getenv("S3_FAILED", "phase1_memory_bank/jobs/failed"),
        workspace=Path(os.getenv("WORKSPACE", "/workspace")),
        af2_weights=Path(os.getenv("AF2_WEIGHTS", "/workspace/af2_weights")),
        num_models=int(os.getenv("NUM_MODELS", "1")),
        num_recycles=int(os.getenv("NUM_RECYCLES", "3")),
        msa_mode=os.getenv("MSA_MODE", "single_sequence"),
        poll_interval=int(os.getenv("POLL_INTERVAL_SEC", "15")),
        max_idle_minutes=int(os.getenv("MAX_IDLE_MINUTES", "30")),
        gpu_id=gpu_id,
        worker_id=worker_id,
    )


def make_s3_client(cfg: WorkerConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        config=Config(signature_version="s3v4"),
    )


def log(cfg: WorkerConfig, msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{ts}] [{cfg.worker_id}] {msg}", flush=True)


def list_pending_jobs(s3, cfg: WorkerConfig) -> list[str]:
    resp = s3.list_objects_v2(Bucket=cfg.bucket, Prefix=cfg.pending_prefix + "/")
    contents = resp.get("Contents", []) or []
    keys = [c["Key"] for c in contents if c["Key"].endswith(".json")]
    return sorted(keys)


def claim_job(s3, cfg: WorkerConfig, pending_key: str) -> Optional[str]:
    job_name = Path(pending_key).name
    processing_key = f"{cfg.processing_prefix}/{job_name}"
    try:
        s3.copy_object(
            Bucket=cfg.bucket,
            CopySource={"Bucket": cfg.bucket, "Key": pending_key},
            Key=processing_key,
            MetadataDirective="REPLACE",
            Metadata={"worker": cfg.worker_id, "claimed_at": str(int(time.time()))},
        )
        s3.delete_object(Bucket=cfg.bucket, Key=pending_key)
        return processing_key
    except Exception as e:
        log(cfg, f"Claim failed for {pending_key}: {e}")
        return None


def download_job(s3, cfg: WorkerConfig, key: str) -> dict:
    obj = s3.get_object(Bucket=cfg.bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def upload_dir(s3, cfg: WorkerConfig, local_dir: Path, remote_prefix: str) -> None:
    for p in local_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(local_dir).as_posix()
        key = f"{remote_prefix}/{rel}"
        s3.upload_file(str(p), cfg.bucket, key)


def upload_json(s3, cfg: WorkerConfig, key: str, data: dict) -> None:
    s3.put_object(
        Bucket=cfg.bucket,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def move_failed(s3, cfg: WorkerConfig, processing_key: str, error: str) -> None:
    job_name = Path(processing_key).name
    failed_key = f"{cfg.failed_prefix}/{job_name}"
    try:
        obj = s3.get_object(Bucket=cfg.bucket, Key=processing_key)
        payload = json.loads(obj["Body"].read().decode("utf-8"))
        payload["_error"] = error
        payload["_worker"] = cfg.worker_id
        payload["_failed_at"] = int(time.time())
        upload_json(s3, cfg, failed_key, payload)
        s3.delete_object(Bucket=cfg.bucket, Key=processing_key)
    except Exception as e:
        log(cfg, f"Failed to move to failed/: {e}")


def write_fasta(seqs: list[dict], fasta_path: Path) -> None:
    with fasta_path.open("w") as f:
        for rec in seqs:
            sid = rec["id"]
            seq = rec["sequence"]
            f.write(f">{sid}\n{seq}\n")


def run_colabfold(
    cfg: WorkerConfig,
    fasta_path: Path,
    out_dir: Path,
    pad_length: Optional[int] = None,
    msa_mode_override: Optional[str] = None,
    msa_host_url: Optional[str] = None,
) -> int:
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(cfg.gpu_id)
    env["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    env["TF_FORCE_UNIFIED_MEMORY"] = "1"

    msa_mode = msa_mode_override or cfg.msa_mode
    cmd = [
        "colabfold_batch",
        "--num-models", str(cfg.num_models),
        "--num-recycle", str(cfg.num_recycles),
        "--msa-mode", msa_mode,
        "--data", str(cfg.af2_weights),
        "--overwrite-existing-results",
    ]

    if msa_host_url and msa_mode != "single_sequence":
        cmd += ["--host-url", msa_host_url]

    if pad_length is not None:
        cmd += ["--pad", str(pad_length)]

    cmd += [str(fasta_path), str(out_dir)]

    log(cfg, f"Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if proc.returncode != 0:
        log(cfg, f"ColabFold stderr: {proc.stderr[-2000:]}")
    return proc.returncode


def parse_plddt(out_dir: Path) -> dict[str, float]:
    scores: dict[str, float] = {}
    for json_path in out_dir.glob("*_scores_rank_001*.json"):
        try:
            payload = json.loads(json_path.read_text())
            plddt_arr = payload.get("plddt", [])
            if plddt_arr:
                stem = json_path.stem
                sid = stem.split("_scores_rank_001")[0]
                scores[sid] = float(sum(plddt_arr) / len(plddt_arr))
        except Exception:
            continue
    return scores


def process_job(s3, cfg: WorkerConfig, processing_key: str) -> None:
    job = download_job(s3, cfg, processing_key)
    job_id = job.get("job_id") or Path(processing_key).stem
    target_id = job.get("target_id", "unknown")
    seqs = job.get("sequences", [])
    pad_length = job.get("pad_length")
    msa_mode_override = job.get("msa_mode")
    msa_host_url = job.get("msa_host_url")

    effective_msa_mode = msa_mode_override or cfg.msa_mode
    log(cfg, f"Job {job_id} (target={target_id}, n={len(seqs)}, pad={pad_length}, msa={effective_msa_mode})")

    with tempfile.TemporaryDirectory(prefix=f"cf_{cfg.worker_id}_") as tmp:
        tmp_path = Path(tmp)
        fasta_path = tmp_path / f"{job_id}.fasta"
        out_dir = tmp_path / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        write_fasta(seqs, fasta_path)

        t0 = time.time()
        rc = run_colabfold(
            cfg,
            fasta_path,
            out_dir,
            pad_length=pad_length,
            msa_mode_override=msa_mode_override,
            msa_host_url=msa_host_url,
        )
        elapsed = time.time() - t0

        if rc != 0:
            raise RuntimeError(f"colabfold_batch exit code {rc}")

        scores = parse_plddt(out_dir)
        log(cfg, f"Folded {len(scores)}/{len(seqs)} sequences in {elapsed:.1f}s")

        remote_prefix = f"{cfg.completed_prefix}/{job_id}"
        upload_dir(s3, cfg, out_dir, f"{remote_prefix}/artifacts")

        result_payload = {
            "job_id": job_id,
            "target_id": target_id,
            "worker_id": cfg.worker_id,
            "gpu_id": cfg.gpu_id,
            "num_sequences": len(seqs),
            "num_folded": len(scores),
            "plddt_scores": scores,
            "elapsed_sec": elapsed,
            "completed_at": int(time.time()),
            "colabfold_settings": {
                "num_models": cfg.num_models,
                "num_recycles": cfg.num_recycles,
                "msa_mode": effective_msa_mode,
                "msa_host_url": msa_host_url,
                "pad_length": pad_length,
            },
        }
        upload_json(s3, cfg, f"{remote_prefix}/result.json", result_payload)

        s3.delete_object(Bucket=cfg.bucket, Key=processing_key)
        log(cfg, f"Job {job_id} completed.")


def main_loop(cfg: WorkerConfig) -> int:
    s3 = make_s3_client(cfg)
    log(cfg, f"Worker started on GPU {cfg.gpu_id}, bucket={cfg.bucket}")

    last_activity = time.time()
    idle_limit = cfg.max_idle_minutes * 60

    while True:
        try:
            pending = list_pending_jobs(s3, cfg)
            if not pending:
                if time.time() - last_activity > idle_limit:
                    log(cfg, f"Idle > {cfg.max_idle_minutes}min, exiting.")
                    return 0
                time.sleep(cfg.poll_interval)
                continue

            for key in pending:
                processing_key = claim_job(s3, cfg, key)
                if processing_key is None:
                    continue
                try:
                    process_job(s3, cfg, processing_key)
                    last_activity = time.time()
                except Exception as e:
                    log(cfg, f"Job failed: {e}\n{traceback.format_exc()}")
                    move_failed(s3, cfg, processing_key, str(e))
                break
        except KeyboardInterrupt:
            log(cfg, "Interrupted, exiting.")
            return 0
        except Exception as e:
            log(cfg, f"Loop error: {e}")
            time.sleep(cfg.poll_interval)


def parse_args():
    p = argparse.ArgumentParser(description="Phase 1 ColabFold GPU Worker")
    p.add_argument("--env", type=Path, default=None, help="Path to .env file")
    p.add_argument("--gpu", type=int, default=None, help="CUDA device id override")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.gpu is not None:
        os.environ["WORKER_GPU_ID"] = str(args.gpu)

    cfg = load_config(args.env)
    return main_loop(cfg)


if __name__ == "__main__":
    sys.exit(main())
