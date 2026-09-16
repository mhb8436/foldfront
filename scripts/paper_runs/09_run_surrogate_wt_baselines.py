#!/usr/bin/env python3
"""Compute WT SoluProt and AF2/ColabFold baselines for paper surrogate runs.

The strict surrogate-triage paper runs intentionally disabled wt_compare during
the expensive candidate-selection benchmark. This helper adds only the WT
baseline artifacts under each existing run directory so Figure 2 can show the
selected candidates relative to the original sequence without regenerating the
ProteinMPNN candidate pool.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = PROJECT_ROOT / "pipeline-mcp" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pipeline_mcp.app import build_runner
from pipeline_mcp.models import SequenceRecord


DEFAULT_RESULTS = PROJECT_ROOT / "data" / "benchmark" / "results"
DEFAULT_RUN_CSV = DEFAULT_RESULTS / "surrogate_triage_budget_run_summary.csv"
DEFAULT_WT_CSV = DEFAULT_RESULTS / "surrogate_triage_wt_metrics.csv"
_HTTP_COLABFOLD_ENV = ("COLABFOLD_URL", "COLABFOLD_HTTP_URL", "COLABFOLD_GPU_URL")


def _load_env(explicit: str | None = None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            PROJECT_ROOT / "pipeline-mcp" / ".env",
            Path("/opt/protein_pipeline/pipeline-mcp/.env"),
        ]
    )
    for env_file in candidates:
        if env_file.exists():
            load_dotenv(str(env_file), override=False)
            return env_file
    return None


def _read_run_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [str(row.get("run_id") or "").strip() for row in csv.DictReader(handle) if str(row.get("run_id") or "").strip()]


def _parse_fasta(path: Path) -> tuple[str, str]:
    header = ""
    seq_parts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if not header:
                header = line[1:].strip()
            continue
        seq_parts.append(line)
    sequence = "".join(seq_parts).replace(" ", "").upper()
    if not sequence:
        raise ValueError(f"empty FASTA sequence: {path}")
    return header or path.parent.name, sequence


def _load_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _target_from_run_id(run_id: str) -> str:
    return run_id.split("_")[-1]


def _compute_one(
    *,
    runner,
    run_dir: Path,
    run_id: str,
    af2_provider: str,
    force: bool,
    dry_run: bool,
) -> dict[str, object]:
    wt_dir = run_dir / "wt"
    metrics_path = wt_dir / "metrics.json"
    cached = _load_json(metrics_path)
    if cached is not None and not force:
        sol = cached.get("soluprot") if isinstance(cached.get("soluprot"), dict) else {}
        af2 = cached.get("af2") if isinstance(cached.get("af2"), dict) else {}
        return {
            "run_id": run_id,
            "target": _target_from_run_id(run_id),
            "sequence_length": cached.get("sequence_length"),
            "wt_soluprot": sol.get("score"),
            "wt_soluprot_passed": sol.get("passed"),
            "wt_plddt": af2.get("best_plddt"),
            "wt_rmsd_ca": af2.get("rmsd_ca"),
            "provider": af2.get("provider"),
            "source": "cached",
        }

    header, sequence = _parse_fasta(run_dir / "target.fasta")
    if dry_run:
        return {
            "run_id": run_id,
            "target": _target_from_run_id(run_id),
            "sequence_length": len(sequence),
            "wt_soluprot": "",
            "wt_soluprot_passed": "",
            "wt_plddt": "",
            "wt_rmsd_ca": "",
            "provider": af2_provider,
            "source": "dry_run",
        }

    wt_dir.mkdir(parents=True, exist_ok=True)
    seqrec = SequenceRecord(id="wt", header=header, sequence=sequence, meta={})

    sol_payload: dict[str, object]
    if runner.soluprot is None:
        sol_payload = {"skipped": True, "reason": "SOLUPROT_URL not set"}
    else:
        scores = runner.soluprot.score([seqrec])
        score = float(scores.get("wt", 0.0))
        sol_payload = {
            "score": score,
            "scores_by_chain": {"chain_1": score},
            "cutoff": 0.5,
            "passed": score >= 0.5,
        }
    _write_json(wt_dir / "soluprot.json", sol_payload)

    af2_client = runner.colabfold if af2_provider == "colabfold" else runner.af2
    if af2_client is None:
        af2_payload: dict[str, object] = {
            "skipped": True,
            "reason": f"{af2_provider} client not configured",
        }
    else:
        af2_dir = wt_dir / "af2"
        af2_dir.mkdir(parents=True, exist_ok=True)

        def _on_job_id(seq_id: str, job_id: str) -> None:
            _write_json(
                af2_dir / "runpod_job.json",
                {"seq_id": seq_id, "job_id": job_id, "provider": af2_provider},
            )

        result = af2_client.predict(
            [seqrec],
            model_preset="monomer",
            db_preset="full_dbs",
            max_template_date="2020-05-14",
            on_job_id=_on_job_id,
        )
        rec = result.get("wt") if isinstance(result, dict) else None
        if not isinstance(rec, dict):
            raise RuntimeError(f"{af2_provider} did not return WT metrics for {run_id}")
        ranked0 = rec.get("ranked_0_pdb") or rec.get("pdb") or rec.get("pdb_text")
        if isinstance(ranked0, str) and ranked0.strip():
            (af2_dir / "ranked_0.pdb").write_text(ranked0, encoding="utf-8")
        if isinstance(rec.get("ranking_debug"), dict):
            _write_json(af2_dir / "ranking_debug.json", rec["ranking_debug"])
        af2_payload = {
            "best_plddt": rec.get("best_plddt"),
            "rmsd_ca": None,
            "model_preset": "monomer",
            "db_preset": "full_dbs",
            "max_template_date": "2020-05-14",
            "provider": af2_provider,
        }
        _write_json(af2_dir / "metrics.json", af2_payload)

    payload = {
        "enabled": True,
        "sequence_source": "target_fasta",
        "sequence_length": len(sequence),
        "soluprot": sol_payload,
        "af2": af2_payload,
    }
    _write_json(metrics_path, payload)
    return {
        "run_id": run_id,
        "target": _target_from_run_id(run_id),
        "sequence_length": len(sequence),
        "wt_soluprot": sol_payload.get("score"),
        "wt_soluprot_passed": sol_payload.get("passed"),
        "wt_plddt": af2_payload.get("best_plddt"),
        "wt_rmsd_ca": af2_payload.get("rmsd_ca"),
        "provider": af2_payload.get("provider"),
        "source": "computed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-csv", default=str(DEFAULT_RUN_CSV))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--out-csv", default=str(DEFAULT_WT_CSV))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--af2-provider", choices=["colabfold", "af2"], default="colabfold")
    parser.add_argument("--af2-backend", choices=["runpod", "http", "auto"], default="runpod")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env_file = _load_env(args.env_file)
    os.environ["PIPELINE_OUTPUT_ROOT"] = str(Path(args.output_root).resolve())
    if args.af2_backend == "runpod":
        for name in _HTTP_COLABFOLD_ENV:
            os.environ.pop(name, None)

    run_ids = _read_run_ids(Path(args.run_csv))
    runner = None if args.dry_run else build_runner()
    rows: list[dict[str, object]] = []
    for run_id in run_ids:
        run_dir = Path(args.output_root).resolve() / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"missing run directory: {run_dir}")
        print(f"[wt] {run_id}", flush=True)
        rows.append(
            _compute_one(
                runner=runner,
                run_dir=run_dir,
                run_id=run_id,
                af2_provider=args.af2_provider,
                force=bool(args.force),
                dry_run=bool(args.dry_run),
            )
        )

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "target",
        "sequence_length",
        "wt_soluprot",
        "wt_soluprot_passed",
        "wt_plddt",
        "wt_rmsd_ca",
        "provider",
        "source",
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"env_file={env_file or 'not found'}")
    print(str(out_csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
