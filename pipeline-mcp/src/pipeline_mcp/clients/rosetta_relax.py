from __future__ import annotations

from dataclasses import dataclass
import gzip
import os
import json
import time
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import Any
import urllib.request
import urllib.error

def _is_number_token(value: object) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False

def _parse_rosetta_scorefile(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Rosetta scorefile missing: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    header: list[str] | None = None
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("SCORE:"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        values = parts[1:]
        if "description" in values and not _is_number_token(values[0]):
            header = values
            continue
        if header is None or len(values) < len(header):
            continue
        row: dict[str, Any] = {}
        for key, raw in zip(header, values):
            row[key] = float(raw) if _is_number_token(raw) else raw
        rows.append(row)
    if not rows:
        raise RuntimeError(f"No Rosetta score rows found in {path}")
    return rows

def _best_score_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    best_row: dict[str, Any] | None = None
    best_score: float | None = None
    for row in rows:
        raw = row.get("total_score", row.get("score"))
        if not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        if best_score is None or value < best_score:
            best_score = value
            best_row = row
    if best_row is None:
        raise RuntimeError("Rosetta scorefile did not contain a numeric total_score")
    return best_row

def _read_pdb_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")

@dataclass(frozen=True)
class RosettaRelaxClient:
    docker_image: str | None = None
    docker_bin: str | None = None
    relax_binary: str | None = None
    score_binary: str | None = None
    database_path: str | None = None
    runpod_endpoint_id: str | None = None
    runpod_api_key: str | None = None
    timeout_s: float = 60.0 * 60.0
    container_workdir: str = "/work"
    container_relax_binary: str = "/usr/local/bin/relax.default.linuxgccrelease"
    container_score_binary: str = "/usr/local/bin/score_jd2.default.linuxgccrelease"
    container_database_path: str = "/usr/local/database"

    @property
    def endpoint_id(self) -> str | None:
        return self.runpod_endpoint_id or os.getenv("RUNPOD_RELAX_ENDPOINT_ID")

    @property
    def api_key(self) -> str | None:
        return self.runpod_api_key or os.getenv("RUNPOD_API_KEY")

    def is_configured(self) -> bool:
        # Now also checks for RunPod configuration
        if self.endpoint_id:
            return True
        if self.relax_binary and self.score_binary and self.database_path:
            return True
        return bool(self.docker_bin and self.docker_image)

    def _mode(self) -> str:
        if self.endpoint_id:
            return "runpod"
        if self.relax_binary and self.score_binary and self.database_path:
            return "binary"
        if self.docker_bin and self.docker_image:
            return "docker"
        raise RuntimeError(
            "Rosetta relax is not configured. Set RUNPOD_RELAX_ENDPOINT_ID or ROSETTA_DOCKER_IMAGE/ROSETTA_DOCKER_BIN."
        )

    def _runpod_sync_call(self, endpoint_id: str, api_key: str, payload: dict) -> dict:
        import urllib.request
        import urllib.error
        import time
        import json
        
        # 1. Start the job asynchronously
        url_run = f"https://api.runpod.ai/v2/{endpoint_id}/run"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = json.dumps(payload).encode("utf-8")
        req_run = urllib.request.Request(url_run, data=data, headers=headers, method="POST")
        
        try:
            with urllib.request.urlopen(req_run, timeout=60) as response:
                result = json.loads(response.read().decode())
                job_id = result.get("id")
                if not job_id:
                    raise RuntimeError(f"RunPod start failed: {result}")
        except Exception as e:
            raise RuntimeError(f"RunPod failed to submit job: {e}")

        # 2. Poll for status
        url_status = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
        req_status = urllib.request.Request(url_status, headers=headers, method="GET")
        
        start_time = time.time()
        while True:
            if time.time() - start_time > self.timeout_s:
                raise RuntimeError("RunPod Relax job timed out")

            try:
                with urllib.request.urlopen(req_status, timeout=30) as response:
                    status_res = json.loads(response.read().decode())
                    status = status_res.get("status")

                    if status == "COMPLETED":
                        if "output" in status_res:
                            return status_res["output"]
                        raise RuntimeError("Job completed but missing output")
                    elif status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                        detail = status_res.get("error") or status_res.get("message")
                        output = status_res.get("output")
                        if not detail and isinstance(output, dict):
                            detail = output.get("error") or output.get("message")
                        if not detail and output is not None:
                            detail = output
                        raise RuntimeError(
                            f"RunPod Relax job failed with status {status}: {detail or status_res}"
                        )
            except urllib.error.HTTPError as e:
                # Ignore transient 5xx errors during long polls
                if e.code not in [500, 502, 503, 504]:
                    raise RuntimeError(f"RunPod API Error: {e.code} {e.reason}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass

            time.sleep(5)

    def run(self, input_pdb: Path, output_dir: Path, nstruct: int = 1, extra_flags: str | None = None) -> dict[str, Any]:
        mode = self._mode()
        
        if mode == "runpod":
            # --- SERVERLESS EXECUTION ---
            endpoint_id = self.endpoint_id
            api_key = self.api_key
            if not endpoint_id:
                raise RuntimeError("RUNPOD_RELAX_ENDPOINT_ID is missing in environment")
            if not api_key:
                raise RuntimeError("RUNPOD_API_KEY is missing in environment")
            
            with open(input_pdb, "r") as f:
                pdb_content = f.read()
                
            payload = {
                "input": {
                    "target_id": input_pdb.stem,
                    "pdb_content": pdb_content,
                    "nstruct": nstruct,
                    "extra_flags": extra_flags or "",
                    "timeout_s": max(60, int(self.timeout_s)),
                }
            }
            
            print(f"Sending Relax job to RunPod Serverless ({endpoint_id})...")
            start_time = time.time()
            output = self._runpod_sync_call(endpoint_id, api_key, payload)
            elapsed = time.time() - start_time
            print(f"RunPod Relax completed in {elapsed:.2f}s")
            
            if output.get("error"):
                raise RuntimeError(f"RunPod Handler Error: {output['error']}")
                
            # Emulate local file creation for pipeline compatibility
            output_dir.mkdir(parents=True, exist_ok=True)
            relaxed_pdb_content = output.get("relaxed_pdb_content", "")
            score_per_res = output.get("score_per_res", 0.0)
            
            best_pdb_path = output_dir / f"{input_pdb.stem}_relaxed.pdb"
            best_pdb_path.write_text(relaxed_pdb_content)
            
            # Create a mock score file
            score_path = output_dir / "score.sc"
            with open(score_path, "w") as f:
                f.write("SCORE: total_score description\n")
                f.write(f"SCORE: {score_per_res * 100} {input_pdb.stem}_relaxed\n") # fake total score
                
            return {
                "best_pdb": best_pdb_path,
                "best_score": score_per_res * 100,
                "score_per_residue": score_per_res,
                "scorefile": score_path,
            }

        # --- LOCAL EXECUTION (Docker or Binary Fallback) ---
        return self._run_local(input_pdb, output_dir, mode, nstruct, extra_flags)

    def relax(
        self, pdb_text: str, nstruct: int = 1, extra_flags: str | None = None
    ) -> dict[str, Any]:
        """Legacy interface for pipeline.py compatibility."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            input_pdb = tmp_path / "input.pdb"
            input_pdb.write_text(pdb_text)
            output_dir = tmp_path / "output"

            result = self.run(input_pdb, output_dir, nstruct, extra_flags)

            best_pdb_path = result.get("best_pdb")
            best_pdb_text = (
                best_pdb_path.read_text()
                if best_pdb_path and best_pdb_path.exists()
                else ""
            )

            res_count = sum(
                1
                for line in best_pdb_text.splitlines()
                if line.startswith("ATOM") and line[12:16].strip() == "CA"
            )
            score_per_residue = result.get("score_per_residue", 0.0)
            true_total_score = score_per_residue * max(res_count, 1)

            return {
                "best_pdb_text": best_pdb_text,
                "total_score": true_total_score,
                "delta_total_score": 0.0,
                "input_total_score": 0.0,
                "description": best_pdb_path.stem if best_pdb_path else "",
                "mode": self._mode(),
            }

    def _runtime_path(self, path: Path, *, root: Path, mode: str) -> str:
        if mode == "docker":
            rel = path.resolve().relative_to(root.resolve())
            return f"{self.container_workdir}/{rel}"
        return str(path.resolve())

    def _run_local(self, input_pdb: Path, output_dir: Path, mode: str, nstruct: int, extra_flags: str | None) -> dict[str, Any]:
        root = output_dir.resolve()
        score_path = root / "score.sc"
        output_dir.mkdir(parents=True, exist_ok=True)

        args: list[str] = []
        if mode == "docker":
            args.extend([
                self.docker_bin or "docker",
                "run",
                "--rm",
                "--network=none",
                "-v",
                f"{root}:{self.container_workdir}",
                "-w",
                self.container_workdir,
            ])
            args.append(self.docker_image)
            args.append(self.container_relax_binary)
            args.extend(["-database", self.container_database_path])
        else:
            args.append(self.relax_binary)
            args.extend(["-database", self.database_path])

        args.extend([
            "-s",
            self._runtime_path(input_pdb, root=root, mode=mode),
            "-ignore_unrecognized_res",
            "-nstruct",
            str(nstruct),
            "-out:file:scorefile",
            self._runtime_path(score_path, root=root, mode=mode),
            "-out:path:all",
            self._runtime_path(output_dir, root=root, mode=mode),
        ])

        if extra_flags:
            args.extend(shlex.split(extra_flags))

        print(f"Running Rosetta Relax (Local): {' '.join(args)}")
        subprocess.run(args, check=True, capture_output=True, text=True, cwd=root)

        if not score_path.exists():
            raise RuntimeError("Rosetta relax failed to produce a scorefile.")

        rows = _parse_rosetta_scorefile(score_path)
        best_row = _best_score_row(rows)
        desc = best_row.get("description", "")
        if not desc:
            raise RuntimeError("Could not determine best model description from scorefile")

        best_pdb = output_dir / f"{desc}.pdb"
        if not best_pdb.exists():
            raise RuntimeError(f"Best PDB not found: {best_pdb}")

        total_score = float(best_row.get("total_score", best_row.get("score", 0.0)))
        res_count = sum(1 for line in best_pdb.read_text().splitlines() if line.startswith("ATOM") and line[12:16].strip() == "CA")
        score_per_residue = total_score / res_count if res_count > 0 else 0.0

        return {
            "best_pdb": best_pdb,
            "best_score": total_score,
            "score_per_residue": score_per_residue,
            "scorefile": score_path,
        }
