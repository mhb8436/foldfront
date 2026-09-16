#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ExpertEntry:
    target_id: str
    model_path: Path
    best_plddt: float
    train_samples: int
    timestamp: float
    target_embedding: Optional[list[float]] = None


@dataclass
class MemoryBank:
    experts: list[ExpertEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.experts)

    def load_from_dir(self, experts_dir: Path, target_embeddings_json: Optional[Path] = None) -> int:
        target_embs: dict[str, list[float]] = {}
        if target_embeddings_json and target_embeddings_json.exists():
            target_embs = json.loads(target_embeddings_json.read_text())

        loaded = 0
        for pkl_path in sorted(experts_dir.glob("expert_*.pkl")):
            try:
                with pkl_path.open("rb") as f:
                    payload = pickle.load(f)
                target_id = payload.get("target_pdb", "unknown")
                entry = ExpertEntry(
                    target_id=target_id,
                    model_path=pkl_path,
                    best_plddt=float(payload.get("best_plddt", 0.0)),
                    train_samples=int(payload.get("train_samples", 0)),
                    timestamp=float(payload.get("timestamp", 0.0)),
                    target_embedding=target_embs.get(target_id),
                )
                self.experts.append(entry)
                loaded += 1
            except Exception as e:
                print(f"Failed to load {pkl_path}: {e}", file=sys.stderr)
        return loaded

    def find_nearest(
        self,
        query_embedding: list[float],
        k: int = 3,
        min_samples: int = 10,
    ) -> list[ExpertEntry]:
        import numpy as np

        candidates = [e for e in self.experts if e.target_embedding is not None and e.train_samples >= min_samples]
        if not candidates:
            return []

        query = np.asarray(query_embedding, dtype=float)
        query_norm = query / (np.linalg.norm(query) + 1e-9)

        scored = []
        for entry in candidates:
            emb = np.asarray(entry.target_embedding, dtype=float)
            emb_norm = emb / (np.linalg.norm(emb) + 1e-9)
            cosine = float(np.dot(query_norm, emb_norm))
            scored.append((cosine, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:k]]

    def ensemble_predict(
        self,
        experts: list[ExpertEntry],
        sequence_embeddings,
    ):
        import numpy as np

        if not experts:
            raise ValueError("No experts provided")

        preds = []
        for entry in experts:
            with entry.model_path.open("rb") as f:
                payload = pickle.load(f)
            rf = payload["model"]
            preds.append(rf.predict(sequence_embeddings))

        preds_arr = np.vstack(preds)
        mean_pred = preds_arr.mean(axis=0)
        std_pred = preds_arr.std(axis=0)
        return mean_pred, std_pred


def parse_args():
    p = argparse.ArgumentParser(description="Memory Bank k-Nearest Experts router (Phase 2)")
    p.add_argument("--experts-dir", type=Path, default=Path("/opt/protein_pipeline/pipeline-mcp/models/experts"))
    p.add_argument("--target-embeddings", type=Path, default=None, help="JSON {target_id: [float,...]}")
    p.add_argument("--query-target", type=str, help="Target ID to find nearest experts for")
    p.add_argument("--query-embedding-file", type=Path, help="JSON with a single embedding list")
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--stats", action="store_true", help="Print memory bank statistics")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    bank = MemoryBank()
    n = bank.load_from_dir(args.experts_dir, args.target_embeddings)
    print(f"Memory Bank loaded {n} experts from {args.experts_dir}")

    if args.stats:
        if not bank.experts:
            print("(empty)")
            return 0
        import statistics

        plddts = [e.best_plddt for e in bank.experts]
        samples = [e.train_samples for e in bank.experts]
        print(f"  pLDDT      mean={statistics.mean(plddts):.1f}  median={statistics.median(plddts):.1f}")
        print(f"  Samples    mean={statistics.mean(samples):.1f}  median={statistics.median(samples):.1f}")
        with_emb = sum(1 for e in bank.experts if e.target_embedding is not None)
        print(f"  With emb   {with_emb}/{len(bank.experts)}")
        oldest = min(bank.experts, key=lambda e: e.timestamp).timestamp
        newest = max(bank.experts, key=lambda e: e.timestamp).timestamp
        print(f"  Date range {time.strftime('%Y-%m-%d', time.gmtime(oldest))} → {time.strftime('%Y-%m-%d', time.gmtime(newest))}")

    if args.query_embedding_file and args.query_embedding_file.exists():
        query = json.loads(args.query_embedding_file.read_text())
        nearest = bank.find_nearest(query, k=args.k)
        print(f"\nTop-{args.k} nearest experts:")
        for rank, entry in enumerate(nearest, 1):
            print(f"  {rank}. target={entry.target_id}  best_plddt={entry.best_plddt:.1f}  samples={entry.train_samples}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
