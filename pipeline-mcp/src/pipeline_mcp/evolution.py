import os
import csv
import hashlib
import json
import shutil
import pickle
import time
import warnings
import numpy as np
from pathlib import Path
from dataclasses import asdict, replace
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import pairwise_distances_argmin_min
from sklearn.pipeline import Pipeline as SkPipeline
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

try:
    import xgboost as xgb
except ImportError:
    xgb = None


_SINGLE_KINDS = ("rf", "ridge", "lightgbm", "xgboost")
_ENSEMBLE_MEMBERS = ("rf", "ridge", "lightgbm", "xgboost")


def _instantiate_single(kind: str, seed: int):
    if kind == "rf":
        return RandomForestRegressor(n_estimators=100, random_state=seed, n_jobs=1)
    if kind == "ridge":
        return SkPipeline([("scaler", StandardScaler()),
                           ("model", Ridge(alpha=1.0, random_state=seed))])
    if kind == "lightgbm":
        if lgb is None:
            raise RuntimeError("lightgbm is not installed but evolution_surrogate_model='lightgbm'")
        return lgb.LGBMRegressor(
            n_estimators=100, num_leaves=7, min_data_in_leaf=2,
            min_data_in_bin=1, learning_rate=0.05,
            random_state=seed, n_jobs=1, verbose=-1,
        )
    if kind == "xgboost":
        if xgb is None:
            raise RuntimeError("xgboost is not installed but evolution_surrogate_model='xgboost'")
        return xgb.XGBRegressor(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            random_state=seed, n_jobs=1, verbosity=0,
        )
    raise ValueError(
        f"Unknown surrogate kind: {kind!r}. "
        f"Expected one of: rf, ridge, lightgbm, xgboost, ensemble."
    )


class _RankMeanEnsemble:
    """Train every member, predict by averaging per-model rank order on test points.

    Rank averaging is scale-invariant and robust to outlier predictions: a model
    that emits a wildly large pLDDT for one candidate cannot drag the consensus,
    because each model only contributes an ordinal position. This matches the
    aggregation rule in scripts/benchmark/10_ensemble_benchmark.py. In the
    23-target benchmark, rank-mean is a robustness option with a small pLDDT
    uplift margin over RF, not the production default.
    """

    def __init__(self, seed: int = 42, members: tuple[str, ...] = _ENSEMBLE_MEMBERS):
        self.seed = seed
        self.members = members
        self.fitted: dict[str, object] = {}

    def fit(self, X, y):
        for kind in self.members:
            try:
                m = _instantiate_single(kind, self.seed)
                m.fit(X, y)
                self.fitted[kind] = m
            except Exception as exc:
                print(f"[ensemble] member {kind} skipped: {exc}")

    def predict(self, X):
        if not self.fitted:
            raise RuntimeError("ensemble has no fitted members; call fit() first")
        ranks = []
        for m in self.fitted.values():
            v = np.asarray(m.predict(X), dtype=np.float64)
            ranks.append(np.argsort(np.argsort(v)).astype(np.float64))
        return np.mean(np.stack(ranks, axis=0), axis=0)


def _make_surrogate(kind: str, seed: int = 42):
    """Return a sklearn-compatible estimator selected by the production benchmark.

    Available kinds (see manuscript Results and the 23-target CATH benchmark):
        rf        - Random Forest, default for AF2/pLDDT triage
        ridge     - Ridge regression, retained for SoluProt-oriented ranking
                    and recall-heavy analyses
        lightgbm  - Gradient boosting comparator
        xgboost   - Gradient boosting alternative
        ensemble  - Rank-mean over RF + Ridge + LightGBM + XGBoost; competitive
                    robustness option for per-target model-switching

    Linear and kernel models are wrapped in a StandardScaler because ESM
    embeddings are zero-mean but not unit-variance per dimension.
    """
    kind = (kind or "rf").lower().strip()
    if kind == "ensemble":
        return _RankMeanEnsemble(seed=seed)
    if kind in _SINGLE_KINDS:
        return _instantiate_single(kind, seed)
    raise ValueError(
        f"Unknown evolution_surrogate_model: {kind!r}. "
        f"Expected one of: {', '.join(_SINGLE_KINDS)}, ensemble."
    )

try:
    import mlflow
except ImportError:
    mlflow = None

try:
    import torch
    from transformers import AutoTokenizer, EsmModel
except ImportError:
    torch = None
    AutoTokenizer = None
    EsmModel = None

from .models import PipelineRequest, PipelineResult
from .storage import init_run, resolve_run_path, set_status, write_json
from .bio.fasta import parse_fasta
from .s3 import ncp_storage

ESM_MODEL_NAME = "facebook/esm2_t6_8M_UR50D"


def _normalize_label_source(value: object) -> str:
    raw = str(value or "experimental").strip().lower().replace("-", "_")
    if raw in {"af2", "computational", "in_silico"}:
        raw = "in_silico_af2"
    if raw not in {"experimental", "in_silico_af2"}:
        raise ValueError(
            "evolution_label_source must be one of: experimental, in_silico_af2"
        )
    return raw


def _event_candidate_ids(entry: dict[str, object]) -> list[str]:
    ids: list[str] = []
    for key in ("candidate_id", "sequence_id", "sample_id"):
        value = str(entry.get(key) or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def _normalize_metric_direction(value: object) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"maximize", "minimize"} else "maximize"


def _normalized_sequence(value: object) -> str:
    return "".join(str(value or "").split()).upper()


def _sequence_label_key(value: object) -> str:
    sequence = _normalized_sequence(value)
    if not sequence:
        return ""
    return "sequence_sha256:" + hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _candidate_sequence_label_keys(run_root: Path) -> dict[str, set[str]]:
    keys_by_id: dict[str, set[str]] = {}

    def add(candidate_id: object, sequence: object) -> None:
        key = _sequence_label_key(sequence)
        cid = str(candidate_id or "").strip()
        if not cid or not key:
            return
        keys_by_id.setdefault(cid, set()).add(key)

    for relative in (
        Path("evolution") / "experiment_request.csv",
        Path("evolution") / "next_candidates.csv",
    ):
        path = run_root / relative
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    sequence = row.get("sequence")
                    for id_key in ("candidate_id", "id", "sequence_id", "sample_id"):
                        add(row.get(id_key), sequence)
        except Exception:
            continue

    summary_path = run_root / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
        for section in ("requested_experiments", "recommended_candidates"):
            rows = summary.get(section) if isinstance(summary, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sequence = row.get("sequence")
                for id_key in ("candidate_id", "id", "sequence_id", "sample_id"):
                    add(row.get(id_key), sequence)
    return keys_by_id


def _event_objective_label(
    entry: dict[str, object], objective_metric: str
) -> dict[str, object] | None:
    metric = str(objective_metric or "activity").strip() or "activity"
    metric_name = str(entry.get("metric_name") or "").strip()
    if metric_name == metric and entry.get("metric_value") is not None:
        try:
            value = float(entry.get("metric_value"))
        except Exception:
            return None
        direction = _normalize_metric_direction(entry.get("metric_direction"))
        return {
            "value": value,
            "metric_direction": direction,
            "selection_score": -value if direction == "minimize" else value,
        }

    metrics = entry.get("metrics")
    if isinstance(metrics, dict) and metrics.get(metric) is not None:
        try:
            value = float(metrics.get(metric))
        except Exception:
            return None
        direction = _normalize_metric_direction(entry.get("metric_direction"))
        return {
            "value": value,
            "metric_direction": direction,
            "selection_score": -value if direction == "minimize" else value,
        }

    result = str(entry.get("result") or "").strip().lower()
    if result == "success":
        return {"value": 1.0, "metric_direction": "maximize", "selection_score": 1.0}
    if result == "fail":
        return {"value": 0.0, "metric_direction": "maximize", "selection_score": 0.0}
    if result == "inconclusive":
        return {"value": 0.5, "metric_direction": "maximize", "selection_score": 0.5}
    return None


def _event_objective_value(entry: dict[str, object], objective_metric: str) -> float | None:
    label = _event_objective_label(entry, objective_metric)
    return None if label is None else float(label["value"])


def _load_experimental_labels(
    output_root: str,
    run_ids: list[str],
    objective_metric: str,
) -> dict[str, dict[str, object]]:
    labels: dict[str, dict[str, object]] = {}
    seen_run_ids: set[str] = set()
    for raw_run_id in run_ids:
        run_id = str(raw_run_id or "").strip()
        if not run_id or run_id in seen_run_ids:
            continue
        seen_run_ids.add(run_id)
        run_root = resolve_run_path(output_root, run_id)
        sequence_keys_by_id = _candidate_sequence_label_keys(run_root)
        path = run_root / "experiments.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            objective_label = _event_objective_label(entry, objective_metric)
            if objective_label is None:
                continue
            direct_sequence_keys = {
                _sequence_label_key(entry.get("sequence")),
                _sequence_label_key(entry.get("sequence_text")),
            }
            for key in ("sequence_hash", "sequence_sha256"):
                raw_hash = str(entry.get(key) or "").strip()
                if raw_hash:
                    direct_sequence_keys.add(
                        raw_hash
                        if raw_hash.startswith("sequence_sha256:")
                        else f"sequence_sha256:{raw_hash}"
                    )
            direct_sequence_keys.discard("")
            for candidate_id in _event_candidate_ids(entry):
                label_payload = {
                    "candidate_id": candidate_id,
                    "value": float(objective_label["value"]),
                    "metric_direction": objective_label["metric_direction"],
                    "selection_score": float(objective_label["selection_score"]),
                    "source_run_id": run_id,
                    "experiment_id": entry.get("id"),
                    "assay_type": entry.get("assay_type"),
                    "result": entry.get("result"),
                    "objective_metric": objective_metric,
                    "matched_by": "candidate_id",
                }
                labels[candidate_id] = label_payload
                for sequence_key in sequence_keys_by_id.get(candidate_id, set()):
                    labels[sequence_key] = {
                        **label_payload,
                        "matched_by": "sequence",
                    }
                for sequence_key in direct_sequence_keys:
                    labels[sequence_key] = {
                        **label_payload,
                        "matched_by": "sequence",
                    }
    return labels


def _write_candidate_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "candidate_id",
        "sequence",
        "soluprot",
        "selection_reason",
        "predicted_objective",
        "predicted_selection_score",
        "metric_direction",
        "label_value",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _query_memory_bank(experts_dir: Path, target_pdb, X_pool, k: int = 3):
    if not experts_dir.exists():
        return None
    if X_pool is None or len(X_pool) == 0:
        return None

    target_name = Path(str(target_pdb)).stem if target_pdb else "unknown"
    expert_files = sorted(experts_dir.glob("expert_*.pkl"))
    if not expert_files:
        return None

    candidates = [p for p in expert_files if target_name not in p.stem]
    if len(candidates) > k * 5:
        candidates = candidates[-k * 5:]

    preds = []
    loaded_targets = []
    for path in candidates[-k:]:
        try:
            with open(path, "rb") as f:
                payload = pickle.load(f)
            expert_model = payload.get("model")
            if expert_model is None:
                continue
            preds.append(expert_model.predict(X_pool))
            loaded_targets.append(payload.get("target_pdb", path.stem))
        except Exception:
            continue

    if not preds:
        return None

    ensemble_mean = np.mean(np.vstack(preds), axis=0)
    return {
        "num_experts": len(preds),
        "expert_targets": loaded_targets,
        "ensemble_mean": ensemble_mean,
    }


def get_esm_embeddings(sequences, device):
    if torch is None or AutoTokenizer is None or EsmModel is None:
        raise RuntimeError(
            "Evolution mode requires torch and transformers to be installed"
        )
    print(f"Loading ESM model {ESM_MODEL_NAME} for embedding extraction...")
    tokenizer = AutoTokenizer.from_pretrained(ESM_MODEL_NAME)
    model = EsmModel.from_pretrained(ESM_MODEL_NAME).to(device)
    model.eval()
    
    embeddings = []
    with torch.no_grad():
        for i in range(0, len(sequences), 16):
            batch_seqs = sequences[i:i+16]
            inputs = tokenizer(batch_seqs, return_tensors="pt", padding=True, truncation=True, max_length=1024)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            outputs = model(**inputs)
            
            mask = inputs['attention_mask'].unsqueeze(-1).expand(outputs.last_hidden_state.size()).float()
            sum_emb = torch.sum(outputs.last_hidden_state * mask, dim=1)
            sum_mask = torch.clamp(mask.sum(dim=1), min=1e-9)
            mean_emb = sum_emb / sum_mask
            embeddings.append(mean_emb.cpu().numpy())
            
    return np.vstack(embeddings)


def _embed_sequences_for_runner(runner, sequences: list[str], device):
    provider = getattr(runner, "esm_embedding", None)
    if provider is not None:
        return provider.embed(sequences)
    return get_esm_embeddings(sequences, device)


def _append_subrun_manifest(parent_root: Path, entry: dict[str, object]) -> None:
    manifest_path = parent_root / "evolution" / "subruns" / "manifest.json"
    manifest: dict[str, object] = {"subruns": []}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except Exception:
            manifest = {"subruns": []}
    entries = manifest.get("subruns")
    if not isinstance(entries, list):
        entries = []
    original_run_id = str(entry.get("original_run_id") or "")
    entries = [
        item
        for item in entries
        if not (
            isinstance(item, dict)
            and str(item.get("original_run_id") or "") == original_run_id
        )
    ]
    entries.append(entry)
    write_json(manifest_path, {"subruns": entries})


def _bundle_evolution_subrun(
    output_root: str,
    parent_root: Path,
    child_run_id: str,
    *,
    category: str,
    round_no: int | None = None,
    phase: str | None = None,
) -> dict[str, object] | None:
    source = resolve_run_path(output_root, child_run_id)
    if not source.exists() or not source.is_dir():
        return None

    subruns_dir = parent_root / "evolution" / "subruns"
    subruns_dir.mkdir(parents=True, exist_ok=True)
    target = subruns_dir / child_run_id
    if target.exists():
        suffix = 1
        while (subruns_dir / f"{child_run_id}_dup{suffix}").exists():
            suffix += 1
        target = subruns_dir / f"{child_run_id}_dup{suffix}"

    shutil.move(str(source), str(target))
    try:
        relative_path = str(target.relative_to(parent_root))
    except ValueError:
        relative_path = str(target)
    entry: dict[str, object] = {
        "original_run_id": child_run_id,
        "category": category,
        "path": relative_path,
        "stored_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    }
    if round_no is not None:
        entry["round"] = int(round_no)
    if phase:
        entry["phase"] = phase
    _append_subrun_manifest(parent_root, entry)
    return entry


def run_evolution(runner, request: PipelineRequest, run_id: str) -> PipelineResult:
    if torch is None:
        raise RuntimeError("Evolution mode requires torch to be installed")

    from .tools import _run_af2_predict, _safe_id

    paths = init_run(runner.output_root, run_id)
    set_status(
        paths,
        stage="evolution",
        state="running",
        detail="Initializing multi-round local active learning evolution",
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    target_pool_size = max(1, int(getattr(request, "evolution_pool_size", 1000)))
    n_train = max(1, int(getattr(request, "evolution_initial_samples", 30)))
    top_k = max(1, int(getattr(request, "evolution_oracle_samples", 20)))
    rounds = max(1, int(getattr(request, "evolution_rounds", 4)))
    soluprot_cutoff = float(getattr(request, "soluprot_cutoff", 0.5))
    surrogate_kind = getattr(request, "evolution_surrogate_model", "rf")
    label_source = _normalize_label_source(
        getattr(request, "evolution_label_source", "experimental")
    )
    objective_metric = (
        str(getattr(request, "evolution_objective_metric", "activity") or "activity")
        .strip()
        or "activity"
    )

    evolution_dir = paths.root / "evolution"
    designs_dir = evolution_dir / "designs"
    designs_dir.mkdir(parents=True, exist_ok=True)

    final_results: list[dict[str, object]] = []
    pool_summaries: list[dict[str, object]] = []
    expert_archives: list[str] = []
    X_labelled_parts: list[np.ndarray] = []
    y_labelled: list[float] = []
    surrogate = None
    experts_dir = Path(__file__).parent.parent.parent / "models" / "experts"
    experts_dir.mkdir(parents=True, exist_ok=True)
    target_name = (
        Path(request.target_pdb).stem
        if getattr(request, "target_pdb", None)
        else "unknown_target"
    )

    def _collect_pool(pool_run_id: str) -> tuple[dict[str, str], dict[str, float], list[str]]:
        pool_dir = resolve_run_path(runner.output_root, pool_run_id)
        all_seqs: dict[str, str] = {}
        soluprot_scores: dict[str, float] = {}

        for tier_dir in sorted((pool_dir / "tiers").glob("*")):
            if not tier_dir.is_dir():
                continue
            fasta_file = next(iter(sorted(tier_dir.glob("designs*.fasta"))), None)
            if fasta_file:
                for rec in parse_fasta(fasta_file.read_text(encoding="utf-8")):
                    all_seqs[rec.id] = rec.sequence

            solu_json = tier_dir / "soluprot.json"
            if solu_json.exists():
                try:
                    data = json.loads(solu_json.read_text(encoding="utf-8"))
                    for sid, score in (data.get("scores") or {}).items():
                        soluprot_scores[str(sid)] = float(score)
                except Exception as exc:
                    print(f"Warning: failed to parse SoluProt scores in {solu_json}: {exc}")

        gated = [
            sid
            for sid, seq in all_seqs.items()
            if soluprot_scores.get(sid, 0.0) >= soluprot_cutoff
        ]
        if not gated:
            gated = list(all_seqs.keys())[:200]
        return all_seqs, soluprot_scores, gated

    def _generate_pool(round_no: int) -> tuple[str, dict[str, str], dict[str, float], list[str], np.ndarray]:
        pool_run_id = f"{run_id}_round{round_no}_pool" if rounds > 1 else f"{run_id}_pool"
        set_status(
            paths,
            stage="evolution",
            state="running",
            detail=f"Round {round_no}/{rounds}: generating candidate pool",
        )
        pool_request = replace(
            request,
            evolution_mode=False,
            stop_after="soluprot",
            num_seq_per_tier=max(100, target_pool_size // 5),
            seed=int(getattr(request, "seed", 0)) + round_no - 1,
        )
        try:
            runner.run(pool_request, run_id=pool_run_id)
        except Exception as exc:
            set_status(
                paths,
                stage="error",
                state="failed",
                detail=f"Round {round_no} pool generation failed: {exc}",
            )
            raise

        all_seqs, soluprot_scores, gated_seq_ids = _collect_pool(pool_run_id)
        if not gated_seq_ids:
            raise RuntimeError(f"Round {round_no} produced no candidate sequences")

        seq_texts = [all_seqs[sid] for sid in gated_seq_ids]
        embeddings = _embed_sequences_for_runner(runner, seq_texts, device)
        pool_summary: dict[str, object] = {
            "round": round_no,
            "pool_run_id": pool_run_id,
            "initial": len(all_seqs),
            "gated": len(gated_seq_ids),
        }
        bundled = _bundle_evolution_subrun(
            runner.output_root,
            paths.root,
            pool_run_id,
            category="pool",
            round_no=round_no,
        )
        if bundled is not None:
            pool_summary["pool_output_path"] = bundled.get("path")
        pool_summaries.append(pool_summary)
        return pool_run_id, all_seqs, soluprot_scores, gated_seq_ids, embeddings

    def _select_kmeans_indices(embeddings: np.ndarray, count: int) -> np.ndarray:
        n_candidates = int(len(embeddings))
        if n_candidates <= count:
            return np.arange(n_candidates)

        kmeans = KMeans(n_clusters=count, random_state=42, n_init=10)
        kmeans.fit(embeddings)
        closest_idx, _ = pairwise_distances_argmin_min(
            kmeans.cluster_centers_, embeddings
        )
        train_idx = np.unique(closest_idx)

        if len(train_idx) < count:
            remaining = np.setdiff1d(np.arange(n_candidates), train_idx)
            rng = np.random.default_rng(42)
            padding = rng.choice(remaining, count - len(train_idx), replace=False)
            train_idx = np.concatenate([train_idx, padding])
        return np.asarray(train_idx, dtype=int)

    if label_source == "experimental":
        set_status(
            paths,
            stage="evolution",
            state="running",
            detail="Generating experimental active-learning candidate pool",
        )
        (
            _pool_run_id,
            all_seqs,
            soluprot_scores,
            gated_seq_ids,
            embeddings,
        ) = _generate_pool(1)

        source_run_ids = [
            str(getattr(request, "evolution_experiment_source_run_id", "") or ""),
            run_id,
        ]
        experimental_labels = _load_experimental_labels(
            runner.output_root, source_run_ids, objective_metric
        )
        labelled_indices: list[int] = []
        y_values: list[float] = []
        label_rows: list[dict[str, object]] = []
        for idx, sid in enumerate(gated_seq_ids):
            label = experimental_labels.get(sid)
            matched_by = "candidate_id" if label else ""
            if not label:
                label = experimental_labels.get(_sequence_label_key(all_seqs[sid]))
                matched_by = "sequence" if label else ""
            if not label:
                continue
            labelled_indices.append(idx)
            y_values.append(float(label.get("selection_score", label["value"])))
            label_rows.append(
                {
                    "id": sid,
                    "sequence": all_seqs[sid],
                    "label_value": float(label["value"]),
                    "selection_score": float(label.get("selection_score", label["value"])),
                    "metric_direction": label.get("metric_direction", "maximize"),
                    "matched_by": matched_by or label.get("matched_by", "candidate_id"),
                    "source_candidate_id": label.get("candidate_id"),
                    "source_run_id": label.get("source_run_id"),
                    "experiment_id": label.get("experiment_id"),
                    "soluprot": float(soluprot_scores.get(sid, 0.0)),
                }
            )

        recommended_rows: list[dict[str, object]] = []
        requested_rows: list[dict[str, object]] = []
        if labelled_indices:
            set_status(
                paths,
                stage="evolution",
                state="running",
                detail=(
                    f"Training experimental surrogate on {len(labelled_indices)} "
                    f"{objective_metric} labels"
                ),
            )
            X_train = embeddings[np.asarray(labelled_indices, dtype=int)]
            y_train = np.asarray(y_values, dtype=np.float64)
            model = _make_surrogate(surrogate_kind, seed=42)
            model.fit(X_train, y_train)
            labelled_set = set(labelled_indices)
            candidate_idx = np.asarray(
                [idx for idx in range(len(gated_seq_ids)) if idx not in labelled_set],
                dtype=int,
            )
            if len(candidate_idx):
                pred = np.asarray(model.predict(embeddings[candidate_idx]), dtype=np.float64)
                metric_directions = [
                    str(row.get("metric_direction") or "maximize") for row in label_rows
                ]
                objective_direction = (
                    "minimize" if "minimize" in metric_directions else "maximize"
                )
                order = np.argsort(pred)[::-1][: min(top_k, len(pred))]
                for rank, local_idx in enumerate(order, start=1):
                    idx = int(candidate_idx[int(local_idx)])
                    sid = gated_seq_ids[idx]
                    selection_score = float(pred[int(local_idx)])
                    row = {
                        "rank": rank,
                        "candidate_id": sid,
                        "id": sid,
                        "sequence": all_seqs[sid],
                        "soluprot": float(soluprot_scores.get(sid, 0.0)),
                        "selection_reason": "experimental_surrogate_top_k",
                        "predicted_objective": (
                            -selection_score
                            if objective_direction == "minimize"
                            else selection_score
                        ),
                        "predicted_selection_score": selection_score,
                        "metric_direction": objective_direction,
                    }
                    recommended_rows.append(row)
            _write_candidate_csv(evolution_dir / "next_candidates.csv", recommended_rows)
        else:
            set_status(
                paths,
                stage="evolution",
                state="running",
                detail="No experimental labels found; selecting bootstrap candidates",
            )
            train_idx = _select_kmeans_indices(embeddings, n_train)
            for rank, idx in enumerate(train_idx, start=1):
                sid = gated_seq_ids[int(idx)]
                requested_rows.append(
                    {
                        "rank": rank,
                        "candidate_id": sid,
                        "id": sid,
                        "sequence": all_seqs[sid],
                        "soluprot": float(soluprot_scores.get(sid, 0.0)),
                        "selection_reason": "kmeans_bootstrap_experiment",
                    }
                )
            _write_candidate_csv(evolution_dir / "experiment_request.csv", requested_rows)

        summary = {
            "run_id": run_id,
            "evolution_mode": "experimental-feedback-active-learning",
            "label_source": "experimental",
            "objective_metric": objective_metric,
            "metric_direction": (
                "minimize"
                if any(str(row.get("metric_direction")) == "minimize" for row in label_rows)
                else "maximize"
            ),
            "surrogate_model": surrogate_kind,
            "pool_statistics": {
                "rounds": 1,
                "initial": len(all_seqs),
                "gated": len(gated_seq_ids),
                "per_round": pool_summaries,
            },
            "experimental_labels": {
                "count": len(label_rows),
                "source_run_ids": [rid for rid in source_run_ids if rid],
                "items": label_rows,
            },
            "requested_experiments": requested_rows,
            "recommended_candidates": recommended_rows,
            "artifact_paths": {
                "experiment_request_csv": (
                    "evolution/experiment_request.csv" if requested_rows else None
                ),
                "next_candidates_csv": (
                    "evolution/next_candidates.csv" if recommended_rows else None
                ),
            },
        }
        write_json(paths.summary_json, summary)
        set_status(
            paths,
            stage="evolution_waiting_for_experiments"
            if requested_rows
            else "evolution_recommendations_ready",
            state="completed",
        )
        try:
            ncp_storage.sync_outputs(run_id)
        except Exception:
            pass
        return PipelineResult(
            run_id=run_id,
            output_dir=str(paths.root),
            msa_a3m_path=None,
            msa_filtered_a3m_path=None,
            msa_tsv_path=None,
            conservation_path=None,
            ligand_mask_path=None,
            surface_mask_path=None,
            tiers=[],
            errors=[],
        )

    def _copy_optional_outputs(eval_run_id: str, sid: str) -> object | None:
        relax_score = None
        eval_path = resolve_run_path(runner.output_root, eval_run_id)
        af2_pdb = list((eval_path / "af2").rglob("*.pdb"))
        if af2_pdb and getattr(runner, "rosetta_relax", None):
            print(f"Executing Serverless Relax for {sid}...")
            relax_out_dir = eval_path / "relax"
            relax_res = runner.rosetta_relax.run(
                af2_pdb[0], relax_out_dir, nstruct=request.relax_nstruct
            )
            relax_score = relax_res.get("score_per_residue")
            if relax_res.get("best_pdb"):
                shutil.copy2(relax_res["best_pdb"], designs_dir / f"{sid}_relaxed.pdb")

        if af2_pdb:
            shutil.copy2(af2_pdb[0], designs_dir / f"{sid}.pdb")
        return relax_score

    def _evaluate_candidate(
        *,
        round_no: int,
        phase: str,
        sid: str,
        seq: str,
        soluprot_score: float,
        predicted_plddt: float | None = None,
        rank: int | None = None,
    ) -> dict[str, object] | None:
        eval_run_id = f"{run_id}_r{round_no}_{phase}_{_safe_id(sid)}"
        try:
            res = _run_af2_predict(
                runner,
                {
                    "run_id": eval_run_id,
                    "target_fasta": f">{sid}\n{seq}\n",
                    "target_pdb": request.target_pdb,
                    "af2_provider": request.af2_provider,
                },
            )
            af2_summary = res.get("summary", {})
            # _run_af2_predict now records per-sequence 5xx/missing-pdb failures
            # in `failures` instead of raising. Evolution treats a failed AF2 the
            # same as before — exclude the sample from the round rather than
            # training the surrogate on a fake plddt=0.0 label.
            if sid in (af2_summary.get("failures") or {}):
                print(f"Oracle skip for {phase} sample {sid}: AF2 failed ({af2_summary['failures'][sid][:160]})")
                return None
            af2_data = af2_summary.get("af2", {}).get(sid, {})
            plddt = float(af2_data.get("best_plddt", 0.0) or 0.0)
            relax_score = _copy_optional_outputs(eval_run_id, sid)
            row: dict[str, object] = {
                "id": sid,
                "round": round_no,
                "phase": f"round_{round_no}_{phase}",
                "plddt": plddt,
                "soluprot": float(soluprot_score),
                "relax_score": relax_score,
            }
            if predicted_plddt is not None:
                row["predicted_plddt"] = float(predicted_plddt)
            if rank is not None:
                row["selection_rank"] = int(rank)
            final_results.append(row)
            return row
        except Exception as exc:
            print(f"Oracle error for {phase} sample {sid}: {exc}")
            return None
        finally:
            _bundle_evolution_subrun(
                runner.output_root,
                paths.root,
                eval_run_id,
                category="oracle",
                round_no=round_no,
                phase=phase,
            )

    def _fit_surrogate() -> object:
        if not X_labelled_parts or not y_labelled:
            raise RuntimeError("Cannot fit evolution surrogate without AF2 labels")
        X_train = np.vstack(X_labelled_parts)
        y_train = np.asarray(y_labelled, dtype=np.float64)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = _make_surrogate(surrogate_kind, seed=42)
            model.fit(X_train, y_train)
        print(
            f"Trained local surrogate: kind={surrogate_kind}, n_train={len(X_train)}"
        )
        return model

    def _archive_expert(model: object, round_no: int) -> None:
        if not y_labelled:
            return
        expert_data = {
            "model": model,
            "model_kind": surrogate_kind,
            "target_pdb": target_name,
            "train_samples": len(y_labelled),
            "best_plddt": float(np.max(np.asarray(y_labelled, dtype=np.float64))),
            "timestamp": time.time(),
            "run_id": run_id,
            "round": round_no,
        }

        run_expert_path = paths.root / f"expert_{surrogate_kind}.pkl"
        try:
            with open(run_expert_path, "wb") as f:
                pickle.dump(expert_data, f)
        except Exception as exc:
            print(f"Warning: Failed to save run expert model: {exc}")

        global_expert_path = (
            experts_dir / f"expert_{target_name}_{run_id}_round{round_no}.pkl"
        )
        try:
            with open(global_expert_path, "wb") as f:
                pickle.dump(expert_data, f)
            expert_archives.append(str(global_expert_path))
            print(f"Archived local expert model to {global_expert_path}")
        except Exception as exc:
            print(f"Warning: Failed to archive global expert model: {exc}")

    print(
        f"Active Learning Config: rounds={rounds}, N_TRAIN={n_train}, "
        f"TOP_K={top_k}, Pool target={target_pool_size}"
    )

    for round_no in range(1, rounds + 1):
        (
            _pool_run_id,
            all_seqs,
            soluprot_scores,
            gated_seq_ids,
            embeddings,
        ) = _generate_pool(round_no)

        if round_no == 1:
            set_status(
                paths,
                stage="evolution",
                state="running",
                detail=f"Round 1/{rounds}: K-means training-set selection",
            )
            train_idx = _select_kmeans_indices(embeddings, n_train)
            train_set = set(int(i) for i in train_idx)
            for idx in train_idx:
                sid = gated_seq_ids[int(idx)]
                row = _evaluate_candidate(
                    round_no=round_no,
                    phase="train",
                    sid=sid,
                    seq=all_seqs[sid],
                    soluprot_score=soluprot_scores.get(sid, 0.0),
                )
                if row is not None:
                    X_labelled_parts.append(embeddings[int(idx) : int(idx) + 1])
                    y_labelled.append(float(row["plddt"]))

            surrogate = _fit_surrogate()
            _archive_expert(surrogate, round_no)
            candidate_idx = np.asarray(
                [i for i in range(len(gated_seq_ids)) if i not in train_set],
                dtype=int,
            )
        else:
            if surrogate is None:
                surrogate = _fit_surrogate()
            candidate_idx = np.arange(len(gated_seq_ids), dtype=int)

        if len(candidate_idx) == 0:
            continue

        set_status(
            paths,
            stage="evolution",
            state="running",
            detail=f"Round {round_no}/{rounds}: ranking pool and evaluating top {top_k}",
        )
        X_pool = embeddings[candidate_idx]
        pred_plddt = np.asarray(surrogate.predict(X_pool), dtype=np.float64)

        memory_bank_boost = None
        if getattr(request, "use_memory_bank", False):
            memory_bank_boost = _query_memory_bank(
                experts_dir, request.target_pdb, X_pool, k=3
            )

        if memory_bank_boost is not None and "ensemble_mean" in memory_bank_boost:
            ensemble = memory_bank_boost["ensemble_mean"]
            if len(ensemble) == len(pred_plddt):
                alpha = 0.6
                pred_plddt = alpha * pred_plddt + (1.0 - alpha) * ensemble
                print(
                    f"Blended local surrogate ({surrogate_kind}) with Memory Bank "
                    f"(alpha={alpha})"
                )

        order = np.argsort(pred_plddt)[::-1][: min(top_k, len(pred_plddt))]
        any_new_label = False
        for rank, local_pred_idx in enumerate(order, start=1):
            idx = int(candidate_idx[int(local_pred_idx)])
            sid = gated_seq_ids[idx]
            row = _evaluate_candidate(
                round_no=round_no,
                phase="top_k",
                sid=sid,
                seq=all_seqs[sid],
                soluprot_score=soluprot_scores.get(sid, 0.0),
                predicted_plddt=float(pred_plddt[int(local_pred_idx)]),
                rank=rank,
            )
            if row is not None:
                X_labelled_parts.append(embeddings[idx : idx + 1])
                y_labelled.append(float(row["plddt"]))
                any_new_label = True

        if any_new_label:
            surrogate = _fit_surrogate()
            _archive_expert(surrogate, round_no)

    def composite_actual(row: dict[str, object]) -> float:
        score = float(row.get("plddt") or 0.0) * 0.4
        score += float(row.get("soluprot") or 0.0) * 30.0
        relax_score = row.get("relax_score")
        if relax_score is not None:
            score -= float(relax_score) * 10.0
        return score

    best_res = (
        max(final_results, key=composite_actual)
        if final_results
        else {"id": "none", "plddt": 0.0}
    )
    total_initial = sum(int(p.get("initial") or 0) for p in pool_summaries)
    total_gated = sum(int(p.get("gated") or 0) for p in pool_summaries)
    oracle_total = len(final_results)
    expected_budget = n_train + top_k * rounds
    summary = {
        "run_id": run_id,
        "evolution_mode": "multi-round-local-active-learning-kmeans",
        "surrogate_model": surrogate_kind,
        "pool_statistics": {
            "rounds": rounds,
            "initial": total_initial,
            "gated": total_gated,
            "per_round": pool_summaries,
            "oracle_train": n_train,
            "oracle_top_k": top_k,
            "oracle_top_k_per_round": top_k,
            "oracle_total": oracle_total,
            "expected_oracle_budget": expected_budget,
            "without_surrogate_oracle_budget": total_gated,
        },
        "budget_model": {
            "round_1": n_train + top_k,
            "later_rounds_each": top_k,
            "with_surrogate": expected_budget,
            "without_surrogate": total_gated,
            "actual_evaluated": oracle_total,
        },
        "expert_archives": expert_archives,
        "best_design": best_res,
        "evaluated_samples": final_results,
    }

    write_json(paths.summary_json, summary)
    set_status(paths, stage="done", state="completed")

    import zipfile

    export_dir = paths.root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    zip_path = export_dir / f"evolution_results_{stamp}.zip"

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(paths.summary_json, arcname="summary.json")
        for expert_path in paths.root.glob("expert_*.pkl"):
            zf.write(expert_path, arcname=expert_path.name)
        for pdb_file in designs_dir.glob("*.pdb"):
            zf.write(pdb_file, arcname=f"designs/{pdb_file.name}")

    summary["archive_name"] = zip_path.name
    write_json(paths.summary_json, summary)

    try:
        ncp_storage.sync_outputs(run_id)
    except Exception:
        pass

    if mlflow is not None:
        try:
            mlflow.set_tracking_uri("http://127.0.0.1:18050")
            mlflow.set_experiment("Pipeline_Evolution_Runs")
            with mlflow.start_run(run_name=f"Evo_{run_id}"):
                mlflow.log_param("run_id", run_id)
                mlflow.log_param("pool_size", total_initial)
                mlflow.log_param("train_size", n_train)
                mlflow.log_param("top_k", top_k)
                mlflow.log_param("rounds", rounds)
                mlflow.log_metric("af2_evaluations", oracle_total)
                mlflow.log_metric("best_plddt", best_res.get("plddt", 0))
                if best_res.get("soluprot") is not None:
                    mlflow.log_metric("best_soluprot", best_res.get("soluprot"))
                if best_res.get("relax_score") is not None:
                    mlflow.log_metric("best_relax", best_res.get("relax_score"))
        except Exception:
            pass

    return PipelineResult(
        run_id=run_id,
        output_dir=str(paths.root),
        msa_a3m_path=None,
        msa_filtered_a3m_path=None,
        msa_tsv_path=None,
        conservation_path=None,
        ligand_mask_path=None,
        surface_mask_path=None,
        tiers=[],
        errors=[],
    )
