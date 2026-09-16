from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Callable
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def scoring_config() -> dict[str, object]:
    base_score = _env_float("PIPELINE_REPORT_BASE_SCORE", 50.0)
    feedback_weight = _env_float("PIPELINE_REPORT_FEEDBACK_WEIGHT", 20.0)
    experiment_weight = _env_float("PIPELINE_REPORT_EXPERIMENT_WEIGHT", 30.0)
    min_score = _env_float("PIPELINE_REPORT_MIN_SCORE", 0.0)
    max_score = _env_float("PIPELINE_REPORT_MAX_SCORE", 100.0)

    evidence_medium_feedback = _env_int("PIPELINE_REPORT_EVIDENCE_MEDIUM_FEEDBACK", 2)
    evidence_high_feedback = _env_int("PIPELINE_REPORT_EVIDENCE_HIGH_FEEDBACK", 6)
    evidence_medium_experiment = _env_int("PIPELINE_REPORT_EVIDENCE_MEDIUM_EXPERIMENT", 1)
    evidence_high_experiment = _env_int("PIPELINE_REPORT_EVIDENCE_HIGH_EXPERIMENT", 3)

    promote_score = _env_float("PIPELINE_REPORT_PROMOTE_SCORE", 75.0)
    promising_score = _env_float("PIPELINE_REPORT_PROMISING_SCORE", 60.0)
    review_score = _env_float("PIPELINE_REPORT_REVIEW_SCORE", 40.0)
    promote_requires_evidence = _env_bool("PIPELINE_REPORT_PROMOTE_REQUIRE_EVIDENCE", True)

    return {
        "base_score": float(base_score),
        "feedback_weight": max(0.0, float(feedback_weight)),
        "experiment_weight": max(0.0, float(experiment_weight)),
        "min_score": float(min_score),
        "max_score": float(max_score),
        "evidence_medium_feedback": max(0, int(evidence_medium_feedback)),
        "evidence_high_feedback": max(0, int(evidence_high_feedback)),
        "evidence_medium_experiment": max(0, int(evidence_medium_experiment)),
        "evidence_high_experiment": max(0, int(evidence_high_experiment)),
        "promote_score": float(promote_score),
        "promising_score": float(promising_score),
        "review_score": float(review_score),
        "promote_requires_evidence": bool(promote_requires_evidence),
    }


def default_score(
    feedback_counts: dict[str, object],
    experiment_counts: dict[str, object],
    cfg: dict[str, object] | None = None,
) -> dict[str, object]:
    cfg = cfg or scoring_config()
    good = int(feedback_counts.get("good") or 0)
    bad = int(feedback_counts.get("bad") or 0)
    fb_total = good + bad

    success = int(experiment_counts.get("success") or 0)
    fail = int(experiment_counts.get("fail") or 0)
    inconclusive = int(experiment_counts.get("inconclusive") or 0)
    exp_total = success + fail + inconclusive

    score = float(cfg["base_score"])
    if fb_total:
        score += ((good - bad) / max(1, fb_total)) * float(cfg["feedback_weight"])
    if exp_total:
        score += ((success - fail) / max(1, exp_total)) * float(cfg["experiment_weight"])

    min_score = float(cfg["min_score"])
    max_score = float(cfg["max_score"])
    if min_score > max_score:
        min_score, max_score = max_score, min_score
    score = max(min_score, min(max_score, score))
    score_int = int(round(score))

    evidence = "low"
    if exp_total >= int(cfg["evidence_high_experiment"]) or fb_total >= int(cfg["evidence_high_feedback"]):
        evidence = "high"
    elif exp_total >= int(cfg["evidence_medium_experiment"]) or fb_total >= int(cfg["evidence_medium_feedback"]):
        evidence = "medium"

    promote_score = float(cfg["promote_score"])
    promising_score = float(cfg["promising_score"])
    review_score = float(cfg["review_score"])
    promote_requires_evidence = bool(cfg["promote_requires_evidence"])

    if score_int >= promote_score and (not promote_requires_evidence or evidence != "low"):
        recommendation = "promote"
    elif score_int >= promising_score:
        recommendation = "promising"
    elif score_int >= review_score:
        recommendation = "needs_review"
    else:
        recommendation = "unlikely"

    return {
        "score": score_int,
        "evidence": evidence,
        "recommendation": recommendation,
        "scoring_config": cfg,
    }


_SCORER_CACHE: dict[str, object] = {"path": None, "mtime": None, "module": None, "fn": None}


def _load_module_from_path(path: str) -> ModuleType:
    file_path = Path(path)
    module_name = f"pipeline_report_scorer_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load scorer module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[call-arg]
    return module


def _resolve_custom_scorer() -> Callable[[dict[str, object], dict[str, object], dict[str, object] | None], dict[str, object]] | None:
    raw = os.environ.get("PIPELINE_REPORT_SCORER", "").strip()
    if not raw:
        return None

    fn_name = os.environ.get("PIPELINE_REPORT_SCORER_FN", "score_report").strip() or "score_report"
    if raw.endswith(".py"):
        path = str(Path(raw).expanduser())
        try:
            mtime = Path(path).stat().st_mtime
        except FileNotFoundError:
            return None

        cached_path = _SCORER_CACHE.get("path")
        cached_mtime = _SCORER_CACHE.get("mtime")
        module = _SCORER_CACHE.get("module")
        if cached_path != path or cached_mtime != mtime or module is None:
            module = _load_module_from_path(path)
            _SCORER_CACHE.update({"path": path, "mtime": mtime, "module": module})
        fn = getattr(module, fn_name, None)
        if callable(fn):
            _SCORER_CACHE["fn"] = fn
            return fn
        return None

    try:
        module = importlib.import_module(raw)
    except Exception:
        return None

    fn = getattr(module, fn_name, None)
    if callable(fn):
        _SCORER_CACHE.update({"path": raw, "module": module, "fn": fn, "mtime": None})
        return fn
    return None


def compute_score(
    feedback_counts: dict[str, object],
    experiment_counts: dict[str, object],
) -> dict[str, object]:
    cfg = scoring_config()
    scorer = _resolve_custom_scorer()
    if scorer is None:
        return default_score(feedback_counts, experiment_counts, cfg)

    try:
        out = scorer(feedback_counts, experiment_counts, cfg)
    except Exception:
        return default_score(feedback_counts, experiment_counts, cfg)
    if not isinstance(out, dict):
        return default_score(feedback_counts, experiment_counts, cfg)
    return {
        "score": out.get("score"),
        "evidence": out.get("evidence"),
        "recommendation": out.get("recommendation"),
        "scoring_config": out.get("scoring_config") or cfg,
    }
