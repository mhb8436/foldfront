from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import os
import threading
from typing import Any
from collections.abc import Callable
from pathlib import Path
import tempfile
import zipfile

import requests

from ..models import SequenceRecord


def _colabfold_max_concurrency() -> int:
    """Per-process cap on concurrent ColabFold/AF2 HTTP requests.

    The shared ColabFold gateway exposes a fixed number of GPU worker slots
    (currently 4). Callers can fan out predict() across many threads (e.g. a
    per-target ThreadPoolExecutor nested inside a multi-target launcher), and
    the product of those two parallelism factors can far exceed the worker
    count. Flooding the gateway with more in-flight requests than it can serve
    only deepens its FIFO queue and, if a caller is killed mid-flight, leaves
    orphaned upstream jobs holding slots. Capping concurrency here keeps the
    client polite regardless of how callers parallelize.

    Configurable via COLABFOLD_MAX_CONCURRENCY (default 4 = worker count).
    A value <= 0 disables throttling.
    """
    raw = os.environ.get("COLABFOLD_MAX_CONCURRENCY", "").strip()
    if not raw:
        return 4
    try:
        return int(raw)
    except ValueError:
        return 4


_COLABFOLD_SEMAPHORE: threading.BoundedSemaphore | None = None
_COLABFOLD_SEMAPHORE_LOCK = threading.Lock()
_COLABFOLD_SEMAPHORE_LIMIT = 0


def _colabfold_gate() -> "threading.BoundedSemaphore | None":
    """Return the process-wide ColabFold concurrency gate, or None if disabled."""
    global _COLABFOLD_SEMAPHORE, _COLABFOLD_SEMAPHORE_LIMIT
    limit = _colabfold_max_concurrency()
    if limit <= 0:
        return None
    if _COLABFOLD_SEMAPHORE is None or _COLABFOLD_SEMAPHORE_LIMIT != limit:
        with _COLABFOLD_SEMAPHORE_LOCK:
            if _COLABFOLD_SEMAPHORE is None or _COLABFOLD_SEMAPHORE_LIMIT != limit:
                _COLABFOLD_SEMAPHORE = threading.BoundedSemaphore(limit)
                _COLABFOLD_SEMAPHORE_LIMIT = limit
    return _COLABFOLD_SEMAPHORE


def _encode_text_file(name: str, content: str) -> dict[str, str]:
    data = str(content or "").encode("utf-8", errors="replace")
    return {"filename": name, "data_b64": base64.b64encode(data).decode("ascii")}


def _omitted_inline_payload(value: object) -> dict[str, object]:
    return {
        "omitted": True,
        "reason": "large inline archive payload",
        "chars": len(value) if isinstance(value, str) else None,
    }


def _strip_inline_archive_payloads(value: object, *, key: str = "") -> object:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for item_key, item_value in value.items():
            item_key_text = str(item_key)
            lower = item_key_text.lower()
            if lower in {"archive_base64", "base64"} or lower.endswith(("_base64", "_b64")):
                out[item_key_text] = _omitted_inline_payload(item_value)
            else:
                out[item_key_text] = _strip_inline_archive_payloads(
                    item_value, key=item_key_text
                )
        return out
    if isinstance(value, list):
        return [_strip_inline_archive_payloads(item, key=key) for item in value]
    return value


def _select_rank1_sdf(names: list[str], complex_name: str | None) -> str | None:
    candidates = [name for name in names if name.lower().endswith("/rank1.sdf") or name.lower().endswith("rank1.sdf")]
    if complex_name:
        preferred = [name for name in candidates if f"/{complex_name}/" in name or name.startswith(f"{complex_name}/")]
        if preferred:
            candidates = preferred
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def _error_detail_from_response(response: Any) -> str | None:
    """Extract the worker's human-readable failure from a non-2xx response.

    GPU HTTP workers report failures as JSON ({"error": ..., "traceback": ...})
    even with an HTTP 500 status. Prefer the structured ``error`` field; fall
    back to a trimmed body so the caller never loses the real cause.
    """
    try:
        body = response.json()
    except Exception:
        text = (getattr(response, "text", "") or "").strip()
        return text[:500] or None
    if isinstance(body, dict):
        for key in ("error", "detail", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:1000]
    return None


# The 20 standard amino acids BioEmu/RFD3 accept (IUPAC). Models reject anything
# else (e.g. 'X' for unknown residues) at input validation, after the request
# has already reached the GPU.
_STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


def validate_protein_sequence(sequence: str, *, model: str) -> None:
    """Fail fast on sequences a structure model cannot accept.

    Raises ValueError with a precise, user-facing message naming the offending
    characters and positions so the failure does not have to round-trip to the
    GPU only to surface as an opaque HTTP 500.
    """
    seq = (sequence or "").strip().upper()
    if not seq:
        raise ValueError(f"{model} requires a non-empty protein sequence.")
    bad = sorted({c for c in seq if c not in _STANDARD_AMINO_ACIDS})
    if bad:
        positions = [i + 1 for i, c in enumerate(seq) if c not in _STANDARD_AMINO_ACIDS]
        preview = ", ".join(str(p) for p in positions[:10])
        if len(positions) > 10:
            preview += ", ..."
        raise ValueError(
            f"{model} only accepts the 20 standard amino acids; sequence "
            f"contains non-standard character(s) {bad} at position(s) {preview}. "
            f"Clean or trim the input sequence before redesign."
        )


_TERMINAL_OK = {"COMPLETED", "SUCCESS", "OK"}
_TERMINAL_PENDING = {"PENDING", "RUNNING"}


@dataclass(frozen=True)
class LocalHttpRunClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _parse_response(self, response: Any, endpoint: str) -> dict[str, Any]:
        status_code = getattr(response, "status_code", 200) or 200
        if status_code >= 400:
            # GPU workers return their real failure (e.g. an input-validation
            # AssertionError) as JSON in the body even on HTTP 500. Surface that
            # instead of the generic "500 Server Error ... for url" that
            # raise_for_status() produces, which reads like a server outage.
            detail = _error_detail_from_response(response)
            if detail:
                raise RuntimeError(f"{endpoint} returned HTTP {status_code}: {detail}")
            response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Local HTTP model response invalid: {data!r}")
        if data.get("error"):
            raise RuntimeError(str(data.get("error")))
        return data

    @staticmethod
    def _extract_output(data: dict[str, Any]) -> dict[str, Any]:
        output = data.get("output")
        if isinstance(output, dict):
            return output
        return data

    def run(
        self,
        payload: dict[str, Any],
        *,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Submit payload and return its output, tolerating both worker styles:

        old (synchronous) workers answer /run with the final COMPLETED result
        directly; current workers answer with PENDING immediately and the
        actual work runs in the background, polled via GET /status?id=<id>.

        Polling (rather than one HTTP call held open for the whole run) is the
        point: a job that legitimately takes tens of minutes (e.g. BioEmu's or
        AlphaFold3's own compute) can outlast an idle-connection timeout on
        some network path between here and the worker. When that happened with
        one long-held POST, the worker finished and tried to reply, but the
        connection was already silently gone -- the result was computed and
        then lost, and the caller hung until its own timeout fired. Each poll
        is short-lived, so a dropped connection just costs one retry, not the
        whole job.
        """
        endpoint = self.base_url.rstrip("/") + "/run"
        # Workers not yet migrated to the async pattern (ColabFold, ESMFold,
        # MMseqs, ANARCII, binder_score) don't answer /run until the job is
        # actually done, which can take far longer than a short fixed
        # timeout -- this call must be able to hold open for the caller's
        # full configured budget, exactly like it did before polling existed.
        # Async workers still return almost instantly, so a large timeout
        # here costs nothing for them.
        response = requests.post(
            endpoint,
            headers=self._headers(),
            json={"input": payload},
            timeout=float(self.timeout_s),
        )
        data = self._parse_response(response, endpoint)
        job_id = str(data.get("id") or data.get("job_id") or "").strip()
        if job_id and on_job_id is not None:
            on_job_id(job_id)
        status = str(data.get("status") or "COMPLETED").upper()
        if status in _TERMINAL_OK:
            return self._extract_output(data)
        if status in _TERMINAL_PENDING:
            if not job_id:
                raise RuntimeError(f"{endpoint} accepted the job but returned no id to poll: {data}")
            return self._poll(job_id)
        raise RuntimeError(f"Local HTTP model job not completed: {data}")

    def _poll(self, job_id: str) -> dict[str, Any]:
        import time

        status_endpoint = self.base_url.rstrip("/") + "/status"
        deadline = time.monotonic() + float(self.timeout_s)
        poll_interval = min(15.0, max(2.0, float(self.timeout_s) / 200))
        consecutive_errors = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"{status_endpoint}?id={job_id} did not complete within {self.timeout_s}s"
                )
            try:
                response = requests.get(
                    status_endpoint,
                    headers=self._headers(),
                    params={"id": job_id},
                    timeout=30.0,
                )
            except requests.RequestException as exc:
                consecutive_errors += 1
                if consecutive_errors > 5:
                    raise RuntimeError(
                        f"{status_endpoint}?id={job_id}: repeated polling failures: {exc}"
                    ) from exc
                time.sleep(min(poll_interval, max(0.0, remaining)))
                continue
            consecutive_errors = 0
            data = self._parse_response(response, status_endpoint)
            status = str(data.get("status") or "").upper()
            if status in _TERMINAL_OK:
                return self._extract_output(data)
            if status == "FAILED":
                raise RuntimeError(str(data.get("error") or f"job {job_id} failed"))
            if status == "NOT_FOUND":
                raise RuntimeError(f"job {job_id} not found by worker (it may have restarted)")
            time.sleep(min(poll_interval, max(0.0, remaining)))


@dataclass(frozen=True)
class LocalHTTPMMseqsClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def _client(self) -> LocalHttpRunClient:
        return LocalHttpRunClient(self.base_url, self.token, self.timeout_s)

    def wait_job(self, job_id: str) -> dict[str, Any]:
        return self._client().run({"task": "wait", "job_id": str(job_id or "").strip()})

    def search(self, *, query_fasta: str, target_db: str = "uniref90", threads: int = 4, use_gpu: bool = True, include_taxonomy: bool = False, return_a3m: bool = False, a3m_max_return_bytes: int = 5 * 1024 * 1024, a3m_format_mode: int = 0, max_seqs: int | None = None, on_job_id: Callable[[str], None] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task": "search",
            "query_fasta": query_fasta,
            "target_db": target_db,
            "threads": int(threads),
            "use_gpu": bool(use_gpu),
            "include_taxonomy": bool(include_taxonomy),
            "return_a3m": bool(return_a3m),
            "a3m_max_return_bytes": int(a3m_max_return_bytes),
            "a3m_format_mode": int(a3m_format_mode),
        }
        if max_seqs is not None:
            payload["max_seqs"] = int(max_seqs)
        output = self._client().run(payload)
        job_id = str(output.get("job_id") or "").strip()
        if job_id and on_job_id:
            on_job_id(job_id)
        return output

    def cluster(self, *, sequences_fasta: str, threads: int = 4, cluster_method: str = "linclust", min_seq_id: float | None = None, coverage: float | None = None, cov_mode: int | None = None, kmer_per_seq: int | None = None, return_representatives: bool = False, on_job_id: Callable[[str], None] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task": "cluster",
            "sequences_fasta": sequences_fasta,
            "threads": int(threads),
            "cluster_method": str(cluster_method or "linclust"),
            "return_representatives": bool(return_representatives),
        }
        for key, value in {
            "min_seq_id": min_seq_id,
            "coverage": coverage,
            "cov_mode": cov_mode,
            "kmer_per_seq": kmer_per_seq,
        }.items():
            if value is not None:
                payload[key] = value
        output = self._client().run(payload)
        job_id = str(output.get("job_id") or "").strip()
        if job_id and on_job_id:
            on_job_id(job_id)
        return output


@dataclass(frozen=True)
class LocalHTTPBioEmuClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def sample(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        on_job_id = payload.pop("on_job_id", None)
        validate_protein_sequence(str(payload.get("sequence") or ""), model="BioEmu")
        return LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(
            payload,
            on_job_id=on_job_id if callable(on_job_id) else None,
        )


@dataclass(frozen=True)
class LocalHTTPRFD3Client:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def design(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        on_job_id = payload.pop("on_job_id", None)
        return LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(
            payload,
            on_job_id=on_job_id if callable(on_job_id) else None,
        )


@dataclass(frozen=True)
class LocalHTTPDiffDockClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def dock(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        on_job_id = payload.pop("on_job_id", None)
        complex_name = str(payload.get("complex_name") or "complex")
        if "protein_ligand_csv" not in payload:
            protein_pdb = str(payload.pop("protein_pdb", "") or "")
            ligand_smiles = payload.pop("ligand_smiles", None)
            ligand_sdf = payload.pop("ligand_sdf", None)
            if not protein_pdb.strip():
                raise ValueError("DiffDock requires protein_pdb text")
            if not (ligand_smiles or ligand_sdf):
                raise ValueError("DiffDock requires ligand_smiles or ligand_sdf")
            config = str(payload.get("config") or "default_inference_args.yaml")
            out_dir = str(payload.get("out_dir") or "results/")
            extra_args = str(payload.get("extra_args") or "")
            protein_name = f"{complex_name}.pdb"
            ligand_name = f"{complex_name}.sdf"
            csv_name = "input_protein_ligand_info.csv"
            ligand_desc = str(ligand_smiles) if ligand_smiles else f"inputs/{ligand_name}"
            csv_text = "\n".join(
                [
                    "complex_name,protein_path,ligand_description,protein_sequence",
                    f"{complex_name},inputs/{protein_name},{ligand_desc},",
                ]
            ) + "\n"
            cmd = f"python3 -m inference --config {config} --protein_ligand_csv data/{csv_name} --out_dir {out_dir}"
            if extra_args:
                cmd = f"{cmd} {extra_args}".strip()
            payload.update(
                {
                    "cmd": cmd,
                    "protein_ligand_csv": _encode_text_file(csv_name, csv_text),
                    "pdb_files": [_encode_text_file(protein_name, protein_pdb)],
                    "sdf_files": [_encode_text_file(ligand_name, str(ligand_sdf))] if ligand_sdf else [],
                    "data_dir": "data",
                    "inputs_dir": "inputs",
                    "out_dir": out_dir,
                    "config": config,
                    "extra_args": extra_args,
                }
            )
        output = LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(
            payload,
            on_job_id=on_job_id if callable(on_job_id) else None,
        )
        if "sdf_text" in output:
            return output
        if "selected_sdf_name" in output:
            return output
        zip_b64 = output.get("out_dir_zip_b64")
        if isinstance(zip_b64, str) and zip_b64.strip():
            zip_bytes = base64.b64decode(zip_b64)
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                names = zf.namelist()
                selected = _select_rank1_sdf(names, complex_name=complex_name)
                if selected:
                    return {
                        "job_id": str(output.get("job_id") or ""),
                        "output": output,
                        "zip_bytes": zip_bytes,
                        "selected_sdf_name": selected,
                        "sdf_text": zf.read(selected).decode("utf-8", errors="replace"),
                    }
        return {
            "job_id": str(output.get("job_id") or ""),
            "output": output,
            "zip_bytes": b"",
            "selected_sdf_name": str(output.get("rank1_sdf") or output.get("sdf_path") or "rank1.sdf"),
            "sdf_text": str(output.get("sdf_text") or output.get("rank1_sdf_text") or ""),
        }


@dataclass(frozen=True)
class LocalHTTPRosettaRelaxClient:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0

    def _call_relax(
        self,
        *,
        target_id: str,
        pdb_text: str,
        nstruct: int = 1,
        extra_flags: str | None = None,
    ) -> dict[str, Any]:
        output = LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(
            {
                "target_id": target_id,
                "pdb_content": pdb_text,
                "nstruct": max(1, int(nstruct or 1)),
                "extra_flags": extra_flags or "",
                "timeout_s": max(60, int(self.timeout_s)),
            }
        )
        relaxed_pdb_text = str(
            output.get("relaxed_pdb_content")
            or output.get("best_pdb_text")
            or output.get("pdb_content")
            or ""
        )
        if not relaxed_pdb_text.strip():
            raise RuntimeError(f"Local HTTP Rosetta relax returned no PDB content: {output}")
        score_per_residue = float(output.get("score_per_res", output.get("score_per_residue", 0.0)) or 0.0)
        return {
            "relaxed_pdb_text": relaxed_pdb_text,
            "score_per_residue": score_per_residue,
        }

    @staticmethod
    def _ca_count(pdb_text: str) -> int:
        return sum(
            1
            for line in pdb_text.splitlines()
            if line.startswith("ATOM") and line[12:16].strip() == "CA"
        )

    def run(
        self,
        input_pdb: Path,
        output_dir: Path,
        nstruct: int = 1,
        extra_flags: str | None = None,
    ) -> dict[str, Any]:
        input_pdb = Path(input_pdb)
        output_dir = Path(output_dir)
        output = self._call_relax(
            target_id=input_pdb.stem,
            pdb_text=input_pdb.read_text(encoding="utf-8"),
            nstruct=nstruct,
            extra_flags=extra_flags,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        best_pdb_path = output_dir / f"{input_pdb.stem}_relaxed.pdb"
        best_pdb_path.write_text(output["relaxed_pdb_text"], encoding="utf-8")
        score_per_residue = float(output["score_per_residue"])
        total_score = score_per_residue * max(self._ca_count(output["relaxed_pdb_text"]), 1)
        score_path = output_dir / "score.sc"
        score_path.write_text(
            "SCORE: total_score description\n"
            f"SCORE: {total_score} {best_pdb_path.stem}\n",
            encoding="utf-8",
        )
        return {
            "best_pdb": best_pdb_path,
            "best_score": total_score,
            "score_per_residue": score_per_residue,
            "scorefile": score_path,
        }

    def relax(
        self,
        pdb_text: str,
        nstruct: int = 1,
        extra_flags: str | None = None,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            input_pdb = tmp_path / "input.pdb"
            input_pdb.write_text(pdb_text, encoding="utf-8")
            result = self.run(input_pdb, tmp_path / "output", nstruct, extra_flags)
            best_pdb_path = result.get("best_pdb")
            best_pdb_text = (
                best_pdb_path.read_text(encoding="utf-8")
                if isinstance(best_pdb_path, Path) and best_pdb_path.exists()
                else ""
            )
            score_per_residue = float(result.get("score_per_residue") or 0.0)
            total_score = score_per_residue * max(self._ca_count(best_pdb_text), 1)
            return {
                "best_pdb_text": best_pdb_text,
                "total_score": total_score,
                "delta_total_score": 0.0,
                "input_total_score": 0.0,
                "description": best_pdb_path.stem if isinstance(best_pdb_path, Path) else "",
                "mode": "http_api",
            }


@dataclass(frozen=True)
class LocalHTTPAlphaFold2Client:
    base_url: str
    token: str | None = None
    timeout_s: float = 21600.0
    endpoint_id: str | None = None

    def predict(self, sequences: list[SequenceRecord], *, model_preset: str = "monomer", db_preset: str = "full_dbs", max_template_date: str = "2020-05-14", extra_flags: str | None = None, on_job_id: Callable[[str, str], None] | None = None, resume_job_ids: dict[str, str] | None = None) -> dict[str, Any]:
        payload = {
            "sequences": [{"id": seq.id, "sequence": seq.sequence} for seq in sequences],
            "model_preset": model_preset,
            "db_preset": db_preset,
            "max_template_date": max_template_date,
            "alphafold_extra_flags": extra_flags,
            "resume_job_ids": resume_job_ids or {},
        }
        gate = _colabfold_gate()
        if gate is None:
            output = LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(payload)
        else:
            with gate:
                output = LocalHttpRunClient(self.base_url, self.token, self.timeout_s).run(payload)
        job_id = str(output.get("job_id") or "").strip()
        if job_id and on_job_id:
            for seq in sequences:
                on_job_id(seq.id, job_id)
        results = output.get("results") if isinstance(output.get("results"), dict) else output
        if all(seq.id in results for seq in sequences):
            stripped = _strip_inline_archive_payloads(results)
            return stripped if isinstance(stripped, dict) else results
        if len(sequences) == 1 and ("ranked_0_pdb" in output or "best_plddt" in output):
            stripped = _strip_inline_archive_payloads(output)
            return {sequences[0].id: stripped if isinstance(stripped, dict) else output}
        stripped = _strip_inline_archive_payloads(results)
        return stripped if isinstance(stripped, dict) else results
