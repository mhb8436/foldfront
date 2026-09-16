from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from .runpod import RunPodClient


@dataclass(frozen=True)
class MMseqsClient:
    runpod: RunPodClient
    endpoint_id: str

    def wait_job(self, job_id: str) -> dict[str, Any]:
        result = self.runpod.wait(self.endpoint_id, job_id)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(f"MMseqs RunPod job not completed: {result}")
        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"MMseqs output missing/invalid: {result}")
        if output.get("error"):
            raise RuntimeError(f"MMseqs error: {output.get('error')}")
        return output

    def search(
        self,
        *,
        query_fasta: str,
        target_db: str = "uniref90",
        threads: int = 4,
        use_gpu: bool = True,
        include_taxonomy: bool = False,
        return_a3m: bool = False,
        a3m_max_return_bytes: int = 5 * 1024 * 1024,
        # NOTE: Many deployments use *indexed* persistent MMseqs DBs on network volumes.
        # MMseqs2 cannot emit CA3M (`--msa-format-mode 1`) from an indexed target DB:
        #   "Cannot use result2msa with indexed target database for CA3M output"
        # Use standard A3M (`--msa-format-mode 0`) by default for compatibility.
        a3m_format_mode: int = 0,
        max_seqs: int | None = None,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task": "search",
            "query_fasta": query_fasta,
            "target_db": target_db,
            "threads": int(threads),
            "use_gpu": bool(use_gpu),
            "include_taxonomy": bool(include_taxonomy),
        }
        if return_a3m:
            payload["return_a3m"] = True
            payload["a3m_max_return_bytes"] = int(a3m_max_return_bytes)
            payload["a3m_format_mode"] = int(a3m_format_mode)
        if max_seqs is not None:
            payload["max_seqs"] = int(max_seqs)

        job_id, _ = self.runpod.run_and_wait_with_job_id(self.endpoint_id, payload, on_job_id=on_job_id)
        return self.wait_job(job_id)

    def cluster(
        self,
        *,
        sequences_fasta: str,
        threads: int = 4,
        cluster_method: str = "linclust",
        min_seq_id: float | None = None,
        coverage: float | None = None,
        cov_mode: int | None = None,
        kmer_per_seq: int | None = None,
        return_representatives: bool = False,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task": "cluster",
            "sequences_fasta": sequences_fasta,
            "threads": int(threads),
            "cluster_method": str(cluster_method or "linclust"),
            "return_representatives": bool(return_representatives),
        }
        if min_seq_id is not None:
            payload["min_seq_id"] = float(min_seq_id)
        if coverage is not None:
            payload["coverage"] = float(coverage)
        if cov_mode is not None:
            payload["cov_mode"] = int(cov_mode)
        if kmer_per_seq is not None:
            payload["kmer_per_seq"] = int(kmer_per_seq)

        job_id, _ = self.runpod.run_and_wait_with_job_id(self.endpoint_id, payload, on_job_id=on_job_id)
        return self.wait_job(job_id)
