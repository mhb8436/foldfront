from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import zipfile
from typing import Any
from collections.abc import Callable

from .runpod import RunPodClient


def _encode_file(name: str, content: str) -> dict[str, str]:
    data = str(content or "").encode("utf-8", errors="replace")
    return {"filename": name, "data_b64": base64.b64encode(data).decode("ascii")}


def _select_rank1_sdf(names: list[str], complex_name: str | None) -> str | None:
    candidates = [n for n in names if n.lower().endswith("/rank1.sdf") or n.lower().endswith("rank1.sdf")]
    if complex_name:
        preferred = [n for n in candidates if f"/{complex_name}/" in n or n.startswith(f"{complex_name}/")]
        if preferred:
            candidates = preferred
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


@dataclass(frozen=True)
class DiffDockRunPodClient:
    runpod: RunPodClient
    endpoint_id: str

    def dock(
        self,
        *,
        protein_pdb: str,
        ligand_smiles: str | None = None,
        ligand_sdf: str | None = None,
        complex_name: str = "complex",
        config: str = "default_inference_args.yaml",
        out_dir: str = "results/",
        extra_args: str | None = None,
        cuda_visible_devices: str | None = None,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not protein_pdb.strip():
            raise ValueError("DiffDock requires protein_pdb text")
        if not (ligand_smiles or ligand_sdf):
            raise ValueError("DiffDock requires ligand_smiles or ligand_sdf")

        protein_name = f"{complex_name}.pdb"
        ligand_name = f"{complex_name}.sdf"
        csv_name = "input_protein_ligand_info.csv"

        ligand_desc = ligand_smiles if ligand_smiles else f"inputs/{ligand_name}"
        csv_lines = [
            "complex_name,protein_path,ligand_description,protein_sequence",
            f"{complex_name},inputs/{protein_name},{ligand_desc},",
        ]
        csv_text = "\n".join(csv_lines) + "\n"

        pdb_files = [_encode_file(protein_name, protein_pdb)]
        sdf_files = [_encode_file(ligand_name, ligand_sdf)] if ligand_sdf else []

        cmd = f"python3 -m inference --config {config} --protein_ligand_csv data/{csv_name} --out_dir {out_dir}"
        if extra_args:
            cmd = f"{cmd} {extra_args}".strip()

        payload: dict[str, Any] = {
            "cmd": cmd,
            "protein_ligand_csv": _encode_file(csv_name, csv_text),
            "pdb_files": pdb_files,
            "sdf_files": sdf_files,
            "data_dir": "data",
            "inputs_dir": "inputs",
            "out_dir": out_dir,
            "config": config,
            "extra_args": extra_args or "",
        }
        if cuda_visible_devices:
            payload["cuda_visible_devices"] = cuda_visible_devices

        job_id, result = self.runpod.run_and_wait_with_job_id(self.endpoint_id, payload, on_job_id=on_job_id)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(f"DiffDock RunPod job not completed: {result}")
        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"DiffDock output missing/invalid: {result}")
        if int(output.get("returncode") or 0) != 0:
            stderr = output.get("stderr") or output
            raise RuntimeError(f"DiffDock failed: {stderr}")

        zip_b64 = output.get("out_dir_zip_b64")
        if not isinstance(zip_b64, str) or not zip_b64.strip():
            raise RuntimeError(f"DiffDock output missing out_dir_zip_b64: {output.keys()}")

        zip_bytes = base64.b64decode(zip_b64)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            selected = _select_rank1_sdf(names, complex_name=complex_name)
            if not selected:
                raise RuntimeError("DiffDock output missing rank1.sdf in zip")
            sdf_text = zf.read(selected).decode("utf-8", errors="replace")

        return {
            "job_id": job_id,
            "output": output,
            "zip_bytes": zip_bytes,
            "selected_sdf_name": selected,
            "sdf_text": sdf_text,
        }
