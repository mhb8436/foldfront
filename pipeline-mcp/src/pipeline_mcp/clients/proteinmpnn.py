from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from typing import Any
from collections.abc import Callable

from .local_http import LocalHttpRunClient
from .runpod import RunPodClient
from ..models import SequenceRecord


def _b64encode_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        return int(default)
    return value if value > 0 else int(default)


@dataclass(frozen=True)
class ProteinMPNNClient:
    runpod: RunPodClient | None
    endpoint_id: str | None
    gpu_url: str | None = None
    gpu_token: str | None = None
    gpu_timeout_s: float = 60.0

    def design(
        self,
        *,
        pdb_text: str,
        pdb_name: str = "input",
        pdb_path_chains: list[str] | str | None = None,
        fixed_positions: dict[str, list[int]] | None = None,
        use_soluble_model: bool = True,
        model_name: str = "v_48_020",
        num_seq_per_target: int = 16,
        batch_size: int = 1,
        sampling_temp: float = 0.1,
        seed: int = 0,
        backbone_noise: float = 0.0,
        on_job_id: Callable[[str], None] | None = None,
    ) -> tuple[SequenceRecord, list[SequenceRecord], dict[str, Any]]:
        total_requested = int(num_seq_per_target)
        max_per_job = _env_int("PROTEINMPNN_MAX_SEQS_PER_JOB", 500)
        if total_requested > max_per_job:
            return self._design_chunked(
                pdb_text=pdb_text,
                pdb_name=pdb_name,
                pdb_path_chains=pdb_path_chains,
                fixed_positions=fixed_positions,
                use_soluble_model=use_soluble_model,
                model_name=model_name,
                num_seq_per_target=total_requested,
                max_seq_per_job=max_per_job,
                batch_size=batch_size,
                sampling_temp=sampling_temp,
                seed=seed,
                backbone_noise=backbone_noise,
                on_job_id=on_job_id,
            )

        payload: dict[str, Any] = {
            "pdb_base64": _b64encode_text(pdb_text),
            "pdb_name": pdb_name,
            "use_soluble_model": bool(use_soluble_model),
            "model_name": model_name,
            "num_seq_per_target": int(num_seq_per_target),
            "batch_size": int(batch_size),
            "sampling_temp": float(sampling_temp),
            "seed": int(seed),
            "backbone_noise": float(backbone_noise),
            "cleanup": True,
        }
        if pdb_path_chains is not None:
            payload["pdb_path_chains"] = pdb_path_chains
        if fixed_positions is not None:
            payload["fixed_positions"] = fixed_positions

        result = self._run_payload(payload, on_job_id=on_job_id)
        return self._parse_result(result)

    def _design_chunked(
        self,
        *,
        pdb_text: str,
        pdb_name: str,
        pdb_path_chains: list[str] | str | None,
        fixed_positions: dict[str, list[int]] | None,
        use_soluble_model: bool,
        model_name: str,
        num_seq_per_target: int,
        max_seq_per_job: int,
        batch_size: int,
        sampling_temp: float,
        seed: int,
        backbone_noise: float,
        on_job_id: Callable[[str], None] | None,
    ) -> tuple[SequenceRecord, list[SequenceRecord], dict[str, Any]]:
        native_rec: SequenceRecord | None = None
        sample_recs: list[SequenceRecord] = []
        chunks: list[dict[str, Any]] = []
        remaining = int(num_seq_per_target)
        chunk_index = 0
        while remaining > 0:
            chunk_index += 1
            chunk_size = min(int(max_seq_per_job), remaining)
            chunk_seed = int(seed) + chunk_index - 1
            payload: dict[str, Any] = {
                "pdb_base64": _b64encode_text(pdb_text),
                "pdb_name": pdb_name,
                "use_soluble_model": bool(use_soluble_model),
                "model_name": model_name,
                "num_seq_per_target": int(chunk_size),
                "batch_size": int(batch_size),
                "sampling_temp": float(sampling_temp),
                "seed": int(chunk_seed),
                "backbone_noise": float(backbone_noise),
                "cleanup": True,
            }
            if pdb_path_chains is not None:
                payload["pdb_path_chains"] = pdb_path_chains
            if fixed_positions is not None:
                payload["fixed_positions"] = fixed_positions

            job_ids: list[str] = []

            def _on_chunk_job(job_id: str) -> None:
                job_ids.append(str(job_id))
                if on_job_id is not None:
                    on_job_id(str(job_id))

            result = self._run_payload(payload, on_job_id=_on_chunk_job)
            chunk_native, chunk_samples, _chunk_output = self._parse_result(result)
            if native_rec is None:
                native_rec = chunk_native
            for sample in chunk_samples:
                sample_index = len(sample_recs) + 1
                meta = dict(sample.meta or {})
                meta.update(
                    {
                        "chunk": chunk_index,
                        "chunk_seed": chunk_seed,
                        "chunk_source_id": sample.id,
                    }
                )
                header = str(sample.header or sample.id)
                sample_recs.append(
                    SequenceRecord(
                        id=f"s{sample_index:05d}",
                        header=f"{header} chunk={chunk_index}",
                        sequence=sample.sequence,
                        meta=meta,
                    )
                )
            chunks.append(
                {
                    "chunk": chunk_index,
                    "num_seq_per_target": chunk_size,
                    "seed": chunk_seed,
                    "job_ids": job_ids,
                    "sample_count": len(chunk_samples),
                }
            )
            remaining -= chunk_size

        if native_rec is None:
            raise RuntimeError("ProteinMPNN chunked run returned no native sequence")
        return (
            native_rec,
            sample_recs,
            {
                "chunked": True,
                "requested_num_seq_per_target": int(num_seq_per_target),
                "max_seq_per_job": int(max_seq_per_job),
                "chunks": chunks,
                "sample_count": len(sample_recs),
            },
        )

    def _run_payload(
        self,
        payload: dict[str, Any],
        *,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if self.gpu_url:
            return self._run_gpu_http(payload, on_job_id=on_job_id)
        if self.runpod is None or not self.endpoint_id:
            raise RuntimeError("ProteinMPNN RunPod client is not configured")
        _, result = self.runpod.run_and_wait_with_job_id(
            self.endpoint_id,
            payload,
            on_job_id=on_job_id,
        )
        return result

    def _parse_result(
        self, result: dict[str, Any]
    ) -> tuple[SequenceRecord, list[SequenceRecord], dict[str, Any]]:
        if result.get("status") != "COMPLETED":
            raise RuntimeError(f"ProteinMPNN job not completed: {result}")
        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"ProteinMPNN output missing/invalid: {result}")

        native = output.get("native") or {}
        samples = output.get("samples") or []
        if not isinstance(native, dict) or not isinstance(samples, list):
            raise RuntimeError(f"ProteinMPNN output malformed: {output}")

        native_rec = SequenceRecord(
            id=str(native.get("name") or "native"),
            header=str(native.get("header") or "native"),
            sequence=str(native.get("sequence") or ""),
            meta={k: v for k, v in native.items() if k not in {"header", "sequence"}},
        )
        sample_recs: list[SequenceRecord] = []
        for i, s in enumerate(samples):
            if not isinstance(s, dict):
                continue
            header = str(s.get("header") or f"sample_{i+1}")
            seq = str(s.get("sequence") or "")
            sample_id = str(s.get("name") or s.get("sample") or f"s{i+1}")
            sample_recs.append(
                SequenceRecord(
                    id=sample_id,
                    header=header,
                    sequence=seq,
                    meta={k: v for k, v in s.items() if k not in {"header", "sequence"}},
                )
            )

        return native_rec, sample_recs, output

    def _run_gpu_http(
        self,
        payload: dict[str, Any],
        *,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self.gpu_url:
            raise RuntimeError("ProteinMPNN GPU URL is not configured")
        # LocalHttpRunClient submits and, if the worker answers PENDING/RUNNING
        # (the current async pattern -- see http_worker_jobs.py on bop), polls
        # GET .../status?id=<job_id> to completion instead of holding one
        # connection open; a worker that still answers /run synchronously
        # short-circuits exactly as before.
        output = LocalHttpRunClient(
            self.gpu_url, self.gpu_token, self.gpu_timeout_s
        ).run(payload, on_job_id=on_job_id)
        if not isinstance(output, dict):
            raise RuntimeError(f"ProteinMPNN GPU output missing/invalid: {output!r}")
        return {"status": "COMPLETED", "output": output}
