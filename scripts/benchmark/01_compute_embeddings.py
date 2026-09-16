#!/usr/bin/env python3
"""
Compute ESM-2 mean-pooled embeddings for every sequence in the master CSV.

Outputs row-aligned numpy arrays so downstream scripts can do
`X = np.load(emb_path); y = df['plddt'].values`.

Models:
    --model 8M   -> facebook/esm2_t6_8M_UR50D    (320D)  default, fast
    --model 150M -> facebook/esm2_t30_150M_UR50D (640D)  slower; for ESM-size ablation

Run twice (once per model) to populate both caches.
"""

from __future__ import annotations

import argparse
import sys
import time
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from transformers import AutoTokenizer, EsmModel

PROJECT_ROOT = Path(os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA_DIR = PROJECT_ROOT / "data" / "benchmark"
CSV_PATH = DATA_DIR / "cath_pilot_dataset.csv"

MODEL_REGISTRY = {
    "8M": ("facebook/esm2_t6_8M_UR50D", "cath_pilot_emb_320d.npy"),
    "150M": ("facebook/esm2_t30_150M_UR50D", "cath_pilot_emb_640d.npy"),
}


def mean_pool(hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    expanded = mask.unsqueeze(-1).expand(hidden.size()).float()
    summed = (hidden * expanded).sum(dim=1)
    counts = expanded.sum(dim=1).clamp(min=1e-9)
    return summed / counts


def embed_sequences(
    sequences: list[str],
    model_name: str,
    batch_size: int,
    device: torch.device,
) -> np.ndarray:
    print(f"Loading {model_name} on {device} ...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = EsmModel.from_pretrained(model_name).to(device).eval()

    all_chunks: list[np.ndarray] = []
    started = time.time()
    with torch.no_grad():
        for start in range(0, len(sequences), batch_size):
            batch = sequences[start : start + batch_size]
            enc = tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=1024,
            )
            enc = {k: v.to(device) for k, v in enc.items()}
            out = model(**enc)
            pooled = mean_pool(out.last_hidden_state, enc["attention_mask"])
            all_chunks.append(pooled.cpu().numpy().astype(np.float32))
            done = start + len(batch)
            if done % (batch_size * 10) == 0 or done == len(sequences):
                elapsed = time.time() - started
                rate = done / max(elapsed, 1e-6)
                eta = (len(sequences) - done) / max(rate, 1e-6)
                print(
                    f"  {done}/{len(sequences)} "
                    f"({rate:.1f} seq/s, ETA {eta:.0f}s)"
                )
    return np.vstack(all_chunks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=sorted(MODEL_REGISTRY.keys()),
        default="8M",
        help="ESM-2 size (default: 8M = 320D)",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute even if the cache file exists.",
    )
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"[fatal] {CSV_PATH} missing - run 00_prepare_data.py first.", file=sys.stderr)
        return 1

    model_name, out_filename = MODEL_REGISTRY[args.model]
    out_path = DATA_DIR / out_filename

    if out_path.exists() and not args.force:
        cached = np.load(out_path)
        print(f"Cache hit: {out_path} shape={cached.shape}. Use --force to recompute.")
        return 0

    df = pd.read_csv(CSV_PATH)
    sequences = df["sequence"].astype(str).tolist()
    print(f"Embedding {len(sequences)} sequences with {model_name}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu":
        print("[note] CUDA not available, running on CPU.")

    embeddings = embed_sequences(sequences, model_name, args.batch_size, device)

    if embeddings.shape[0] != len(df):
        print(
            f"[fatal] embedding count {embeddings.shape[0]} "
            f"!= csv rows {len(df)}",
            file=sys.stderr,
        )
        return 2

    np.save(out_path, embeddings)
    print(f"Saved {out_path} shape={embeddings.shape} dtype={embeddings.dtype}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
