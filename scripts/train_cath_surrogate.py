#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import pickle
import time

import numpy as np


PROJECT_ROOT = Path("/opt/protein_pipeline")
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
META_ROOT = PROJECT_ROOT / "meta_surrogate_prototype"
MODEL_ROOT = PROJECT_ROOT / "pipeline-mcp" / "models"
VALID_SUBSETS = ("train", "val", "test")
DATASET_FIELDNAMES = [
    "run_id",
    "subset",
    "tier",
    "seq_id",
    "sequence",
    "soluprot",
    "plddt",
    "relax",
    "rmsd",
    "target_rmsd",
]


def _iso_suffix() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _safe_tier_key(value: object) -> str:
    try:
        return f"{int(round(float(value) * 100)):d}"
    except Exception:
        return str(value)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _numeric_dict(payload: object) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def _extract_dataset(subsets: list[str]) -> list[dict[str, object]]:
    wanted_prefixes = tuple(f"cath_{subset}_" for subset in subsets)
    rows: list[dict[str, object]] = []

    if not OUTPUTS_ROOT.exists():
        return rows

    for run_root in sorted(OUTPUTS_ROOT.iterdir()):
        if not run_root.is_dir():
            continue
        run_id = run_root.name
        if not run_id.startswith(wanted_prefixes):
            continue
        summary_path = run_root / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = _read_json(summary_path)
        except Exception as exc:
            print(f"[skip] failed to read {summary_path}: {exc}")
            continue
        if not isinstance(summary, dict):
            continue

        if run_id.startswith("cath_train_"):
            subset = "train"
        elif run_id.startswith("cath_val_"):
            subset = "val"
        else:
            subset = "test"

        tiers = summary.get("tiers")
        if not isinstance(tiers, list):
            continue

        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            tier_key = _safe_tier_key(tier.get("tier"))
            tier_dir = run_root / "tiers" / tier_key
            soluprot_payload = _load_json_dict(tier_dir / "soluprot.json")
            af2_payload = _load_json_dict(tier_dir / "af2_scores.json")
            relax_payload = _load_json_dict(tier_dir / "relax_scores.json")
            soluprot_scores = _numeric_dict(soluprot_payload.get("scores"))
            plddt_scores = _numeric_dict(af2_payload.get("scores"))
            relax_scores = _numeric_dict(relax_payload.get("score_per_residue"))
            rmsd_scores = _numeric_dict(af2_payload.get("rmsd_scores"))
            target_rmsd_scores = _numeric_dict(af2_payload.get("target_rmsd_scores"))
            samples = tier.get("proteinmpnn_samples")
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                seq_id = str(sample.get("id") or "").strip()
                sequence = str(sample.get("sequence") or "").strip()
                if not seq_id or not sequence:
                    continue
                rows.append(
                    {
                        "run_id": run_id,
                        "subset": subset,
                        "tier": tier.get("tier"),
                        "seq_id": seq_id,
                        "sequence": sequence,
                        "soluprot": soluprot_scores.get(seq_id),
                        "plddt": plddt_scores.get(seq_id),
                        "relax": relax_scores.get(seq_id),
                        "rmsd": rmsd_scores.get(seq_id),
                        "target_rmsd": target_rmsd_scores.get(seq_id),
                    }
                )

    deduped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        deduped[(str(row["run_id"]), str(row["seq_id"]))] = row
    return list(deduped.values())


def _generate_embeddings(sequences: list[str], model_name: str) -> np.ndarray:
    import torch
    from transformers import AutoTokenizer
    from transformers import EsmModel

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = EsmModel.from_pretrained(model_name)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Embedding model: {model_name}")
    print(f"Embedding device: {device}")
    model.to(device)
    model.eval()

    all_batches: list[np.ndarray] = []
    batch_size = 16

    with torch.no_grad():
        for start in range(0, len(sequences), batch_size):
            batch = sequences[start : start + batch_size]
            encoded = tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            outputs = model(**encoded)
            hidden = outputs.last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
            summed = torch.sum(hidden * mask, dim=1)
            counts = torch.clamp(mask.sum(dim=1), min=1e-9)
            pooled = summed / counts
            all_batches.append(pooled.cpu().numpy())

    return np.vstack(all_batches)


def _train_regressor(X: np.ndarray, y: np.ndarray, *, hidden_layers: tuple[int, ...]) -> object:
    from sklearn.neural_network import MLPRegressor

    model = MLPRegressor(hidden_layer_sizes=hidden_layers, max_iter=500, random_state=42)
    model.fit(X, y)
    return model


def _write_pickle(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def _parse_subsets(raw: str) -> list[str]:
    out: list[str] = []
    for item in str(raw or "").split(","):
        subset = item.strip().lower()
        if not subset:
            continue
        if subset not in VALID_SUBSETS:
            raise ValueError(f"invalid subset: {subset}")
        if subset not in out:
            out.append(subset)
    return out


def _write_dataset_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATASET_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in DATASET_FIELDNAMES})


def _count_not_null(rows: list[dict[str, object]], field: str) -> int:
    return sum(1 for row in rows if isinstance(row.get(field), (int, float)))


def _mask_and_targets(
    rows: list[dict[str, object]],
    field: str,
) -> tuple[np.ndarray, np.ndarray]:
    mask = np.array(
        [isinstance(row.get(field), (int, float)) for row in rows],
        dtype=bool,
    )
    targets = np.array(
        [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))],
        dtype=float,
    )
    return mask, targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Train CATH surrogate models from local pipeline outputs")
    parser.add_argument("--subsets", default="train,val,test", help="Comma-separated subset list")
    parser.add_argument("--embedding-model", default="facebook/esm2_t6_8M_UR50D")
    args = parser.parse_args()

    subsets = _parse_subsets(args.subsets)
    if not subsets:
        raise SystemExit("at least one subset is required")

    META_ROOT.mkdir(parents=True, exist_ok=True)
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)

    print(f"Collecting local outputs for subsets: {', '.join(subsets)}")
    rows = _extract_dataset(subsets)
    if not rows:
        print("No local tier data found for the requested subsets.")
        return 2

    run_count = len({str(row["run_id"]) for row in rows})
    label_counts = {
        "soluprot": _count_not_null(rows, "soluprot"),
        "plddt": _count_not_null(rows, "plddt"),
        "relax": _count_not_null(rows, "relax"),
        "rmsd": _count_not_null(rows, "rmsd"),
        "target_rmsd": _count_not_null(rows, "target_rmsd"),
    }

    print(f"Collected {len(rows)} unique sequences from {run_count} runs.")
    print(label_counts)

    tag = "-".join(subsets)
    csv_path = META_ROOT / f"extracted_data_cath_{tag}.csv"
    npy_path = META_ROOT / f"embeddings_cath_{tag}.npy"
    latest_csv = META_ROOT / "extracted_data_full.csv"
    latest_npy = META_ROOT / "embeddings.npy"
    _write_dataset_csv(rows, csv_path)
    _write_dataset_csv(rows, latest_csv)
    print(f"Saved dataset to {csv_path}")

    embeddings = _generate_embeddings(
        [str(row["sequence"]) for row in rows],
        args.embedding_model,
    )
    np.save(npy_path, embeddings)
    np.save(latest_npy, embeddings)
    print(f"Saved embeddings to {npy_path} with shape {embeddings.shape}")

    solu_mask, solu_targets = _mask_and_targets(rows, "soluprot")
    if len(solu_targets) <= 0:
        print("SoluProt labels are missing; aborting training.")
        return 3
    solu_model = _train_regressor(
        embeddings[solu_mask],
        solu_targets,
        hidden_layers=(256, 128),
    )
    solu_path = MODEL_ROOT / "global_soluprot_v1.pkl"
    _write_pickle(solu_path, solu_model)
    print(f"Exported {solu_path}")

    plddt_path: str | None = None
    plddt_mask, plddt_targets = _mask_and_targets(rows, "plddt")
    if len(plddt_targets) >= 20:
        plddt_model = _train_regressor(
            embeddings[plddt_mask],
            plddt_targets,
            hidden_layers=(128, 64),
        )
        plddt_target = MODEL_ROOT / "global_plddt_v1.pkl"
        _write_pickle(plddt_target, plddt_model)
        plddt_path = str(plddt_target)
        print(f"Exported {plddt_target}")
    else:
        print("Skipped pLDDT model export because fewer than 20 labels are available.")

    relax_path: str | None = None
    relax_mask, relax_targets = _mask_and_targets(rows, "relax")
    if len(relax_targets) >= 20:
        relax_model = _train_regressor(
            embeddings[relax_mask],
            relax_targets,
            hidden_layers=(128, 64),
        )
        relax_target = MODEL_ROOT / "global_relax_v1.pkl"
        _write_pickle(relax_target, relax_model)
        relax_path = str(relax_target)
        print(f"Exported {relax_target}")
    else:
        print("Skipped relax model export because fewer than 20 labels are available.")

    summary = {
        "subsets": subsets,
        "records": int(len(rows)),
        "runs": int(run_count),
        "label_counts": label_counts,
        "artifacts": {
            "dataset_csv": str(csv_path),
            "dataset_csv_latest": str(latest_csv),
            "embeddings_npy": str(npy_path),
            "embeddings_npy_latest": str(latest_npy),
            "soluprot_model": str(solu_path),
            "plddt_model": plddt_path,
            "relax_model": relax_path,
        },
        "completed_at": _iso_suffix(),
    }
    summary_path = META_ROOT / f"training_summary_{tag}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Training summary written to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
