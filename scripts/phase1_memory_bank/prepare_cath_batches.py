#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "pipeline-mcp" / "src"))

from pipeline_mcp.clients.proteinmpnn import ProteinMPNNClient
from pipeline_mcp.clients.runpod import RunPodClient
from pipeline_mcp.clients.soluprot import SoluProtClient
from pipeline_mcp.models import SequenceRecord


def load_env(env_file: Path) -> None:
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def iter_cath_targets(root: Path, limit: int | None = None) -> Iterable[Path]:
    paths = sorted(root.glob("*.pdb"))
    if limit is not None:
        paths = paths[:limit]
    yield from paths


def safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def generate_mpnn(
    mpnn: ProteinMPNNClient,
    pdb_path: Path,
    num_seqs: int,
    seed: int = 0,
) -> list[SequenceRecord]:
    pdb_text = pdb_path.read_text()
    _, samples, _ = mpnn.design(
        pdb_text=pdb_text,
        pdb_name=pdb_path.stem,
        num_seq_per_target=num_seqs,
        sampling_temp=0.1,
        seed=seed,
        use_soluble_model=True,
    )
    return samples


def score_soluprot(
    solu: SoluProtClient,
    samples: list[SequenceRecord],
) -> dict[str, float]:
    return solu.score(samples)


def esm_embed(sequences: list[str], device: str = "cuda") -> "np.ndarray":
    import numpy as np
    import torch
    from transformers import AutoTokenizer, EsmModel

    model_name = os.getenv("ESM_MODEL", "facebook/esm2_t6_8M_UR50D")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = EsmModel.from_pretrained(model_name)

    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    model = model.to(device)
    model.eval()

    all_embs: list[np.ndarray] = []
    batch_size = 16
    with torch.no_grad():
        for i in range(0, len(sequences), batch_size):
            batch = sequences[i : i + batch_size]
            enc = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=1024)
            enc = {k: v.to(device) for k, v in enc.items()}
            out = model(**enc)
            mask = enc["attention_mask"].unsqueeze(-1).expand(out.last_hidden_state.size()).float()
            pooled = (out.last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            all_embs.append(pooled.cpu().numpy())
    return np.vstack(all_embs)


def kmeans_sample(embeddings, k: int, seed: int = 42):
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import pairwise_distances_argmin_min

    k = min(k, len(embeddings))
    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    km.fit(embeddings)
    closest, _ = pairwise_distances_argmin_min(km.cluster_centers_, embeddings)
    return np.unique(closest)


def process_target(
    pdb_path: Path,
    mpnn: ProteinMPNNClient,
    solu: SoluProtClient,
    num_mpnn: int,
    soluprot_cutoff: float,
    num_seeds: int,
    embedding_device: str,
) -> dict:
    target_id = safe_id(pdb_path.stem)
    t0 = time.time()

    samples = generate_mpnn(mpnn, pdb_path, num_mpnn)
    if not samples:
        return {"target_id": target_id, "error": "mpnn returned 0 samples"}

    solu_scores = score_soluprot(solu, samples)
    gated = [s for s in samples if solu_scores.get(s.id, 0.0) >= soluprot_cutoff]
    if not gated:
        gated = samples[: max(100, num_seeds * 5)]

    import numpy as np

    seqs = [s.sequence for s in gated]
    embeddings = esm_embed(seqs, device=embedding_device)

    picked = kmeans_sample(embeddings, num_seeds)
    selected = [gated[i] for i in picked]
    selected_embs = embeddings[picked]

    elapsed = time.time() - t0
    return {
        "target_id": target_id,
        "selected": [
            {
                "id": f"{target_id}__{s.id}",
                "sequence": s.sequence,
                "soluprot": float(solu_scores.get(s.id, 0.0)),
            }
            for s in selected
        ],
        "embeddings": selected_embs.tolist(),
        "elapsed_sec": elapsed,
        "mpnn_count": len(samples),
        "gated_count": len(gated),
    }


def write_outputs(
    results: list[dict],
    fasta_out: Path,
    csv_out: Path,
    emb_out: Path,
) -> None:
    fasta_out.parent.mkdir(parents=True, exist_ok=True)
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    emb_out.parent.mkdir(parents=True, exist_ok=True)

    with fasta_out.open("w") as ff, csv_out.open("w", newline="") as fc, emb_out.open("w", newline="") as fe:
        csv_writer = csv.DictWriter(fc, fieldnames=["seq_id", "target_id", "sequence", "soluprot"])
        csv_writer.writeheader()

        emb_writer = csv.writer(fe)
        emb_writer.writerow(["seq_id", "embedding"])

        for result in results:
            if "error" in result:
                continue
            target_id = result["target_id"]
            selected = result["selected"]
            embs = result["embeddings"]
            for rec, emb in zip(selected, embs):
                sid = rec["id"]
                ff.write(f">{sid}\n{rec['sequence']}\n")
                csv_writer.writerow({
                    "seq_id": sid,
                    "target_id": target_id,
                    "sequence": rec["sequence"],
                    "soluprot": rec["soluprot"],
                })
                emb_writer.writerow([sid, ",".join(f"{x:.6f}" for x in emb)])


def parse_args():
    p = argparse.ArgumentParser(
        description="Preprocess CATH targets: MPNN + SoluProt + ESM + K-means → FASTA/CSV for submit_batches.py"
    )
    p.add_argument("--cath-dir", type=Path, required=True, help="Directory containing CATH *.pdb files")
    p.add_argument("--out-dir", type=Path, default=Path("/opt/protein_pipeline/phase1_input"))
    p.add_argument("--env", type=Path, default=Path(__file__).parent / ".env")
    p.add_argument("--num-mpnn", type=int, default=100, help="Sequences per target (original: 1000, reduced default)")
    p.add_argument("--soluprot-cutoff", type=float, default=0.5)
    p.add_argument("--num-seeds", type=int, default=30, help="K-means centers to select")
    p.add_argument("--limit", type=int, default=None, help="Process only N targets (for dry-run)")
    p.add_argument("--workers", type=int, default=4, help="Parallel targets (mind RunPod concurrency caps)")
    p.add_argument("--embedding-device", choices=("cuda", "cpu"), default="cpu")
    p.add_argument("--resume-from", type=Path, default=None, help="Manifest JSON to resume from")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    load_env(args.env)
    load_env(PROJECT_ROOT / ".env")

    runpod_key = os.environ.get("RUNPOD_API_KEY")
    mpnn_endpoint = os.environ.get("PROTEINMPNN_ENDPOINT_ID")
    soluprot_url = os.environ.get("SOLUPROT_URL")

    if not runpod_key or not mpnn_endpoint:
        print("Error: RUNPOD_API_KEY and PROTEINMPNN_ENDPOINT_ID required", file=sys.stderr)
        return 2
    if not soluprot_url:
        print("Error: SOLUPROT_URL required", file=sys.stderr)
        return 2

    runpod = RunPodClient(api_key=runpod_key)
    mpnn = ProteinMPNNClient(runpod=runpod, endpoint_id=mpnn_endpoint)
    solu = SoluProtClient(url=soluprot_url)

    targets = list(iter_cath_targets(args.cath_dir, args.limit))
    if not targets:
        print(f"No PDBs found in {args.cath_dir}", file=sys.stderr)
        return 2

    processed_ids: set[str] = set()
    if args.resume_from and args.resume_from.exists():
        manifest = json.loads(args.resume_from.read_text())
        processed_ids = set(manifest.get("completed", []))
        print(f"Resuming, skipping {len(processed_ids)} targets already done")

    remaining = [p for p in targets if safe_id(p.stem) not in processed_ids]
    print(f"Processing {len(remaining)} / {len(targets)} targets with {args.workers} parallel workers")

    results: list[dict] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(
                process_target,
                p,
                mpnn,
                solu,
                args.num_mpnn,
                args.soluprot_cutoff,
                args.num_seeds,
                args.embedding_device,
            ): p
            for p in remaining
        }
        for i, fut in enumerate(as_completed(futures), 1):
            pdb = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                status = "OK" if "error" not in res else f"ERR: {res['error']}"
                print(f"[{i}/{len(remaining)}] {pdb.stem}: {status} ({res.get('elapsed_sec', 0):.1f}s)", flush=True)
                if "error" in res:
                    failures.append(pdb.stem)
            except Exception as e:
                print(f"[{i}/{len(remaining)}] {pdb.stem}: FAILED {e}", file=sys.stderr, flush=True)
                failures.append(pdb.stem)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())

    fasta_out = args.out_dir / f"phase1_seeds_{stamp}.fasta"
    csv_out = args.out_dir / f"phase1_seeds_{stamp}.csv"
    emb_out = args.out_dir / f"phase1_embeddings_{stamp}.csv"

    write_outputs(results, fasta_out, csv_out, emb_out)

    manifest = {
        "timestamp": stamp,
        "num_targets": len(targets),
        "num_processed": len([r for r in results if "error" not in r]),
        "num_failed": len(failures),
        "failed_targets": failures,
        "completed": [r["target_id"] for r in results if "error" not in r],
        "outputs": {
            "fasta": str(fasta_out),
            "csv": str(csv_out),
            "embeddings_csv": str(emb_out),
        },
        "params": {
            "num_mpnn": args.num_mpnn,
            "soluprot_cutoff": args.soluprot_cutoff,
            "num_seeds": args.num_seeds,
        },
    }

    manifest_out = args.out_dir / f"phase1_manifest_{stamp}.json"
    manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"\nDone. Success: {manifest['num_processed']}, Failed: {manifest['num_failed']}")
    print(f"  FASTA:      {fasta_out}")
    print(f"  Seeds CSV:  {csv_out}")
    print(f"  Embed CSV:  {emb_out}")
    print(f"  Manifest:   {manifest_out}")
    print(f"\nNext: python3 submit_batches.py {fasta_out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
