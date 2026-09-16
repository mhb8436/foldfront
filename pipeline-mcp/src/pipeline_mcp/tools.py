from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from dataclasses import replace
import copy
import csv
import base64
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile
from typing import Any
from typing import Callable

from .af2_utils import af2_error_is_missing_pdb_outputs
from .af2_utils import af2_error_is_server_failure
from .bio.fasta import FastaRecord
from .bio.fasta import parse_fasta
from .bio.ligand_text import normalize_diffdock_ligand_inputs
from .bio.pdb import normalize_structure_text
from .bio.pdb import residues_by_chain
from .bio.residue_exposure import classify_residues as _classify_residues
from .bio.sdf import append_ligand_pdb
from .bio.sdf import sdf_to_pdb
from .bio.structure_fetch import resolve_structure_input
from .auth import AuthError
from .cath_ops import job_kind_batch
from .cath_ops import job_kind_train
from .cath_ops import launch_cath_batch_job
from .cath_ops import launch_cath_training_job
from .cath_ops import list_managed_jobs
from .cath_ops import read_managed_job
from .cath_ops import read_managed_job_log
from .cath_ops import stop_managed_job
from .cath_ops import delete_managed_job
from .cath_ops import summarize_all_subsets
from .models import PipelineRequest
from .models import SequenceRecord
from .pipeline import PipelineRunner
from .pipeline import PipelineCancelled
from .pipeline import _AF2_MULTIMER_CHAIN_DELIMITER
from .pipeline import _dummy_backbone_pdb
from .pipeline import _normalize_af2_provider
from .pipeline import _prepare_af2_sequence
from .pipeline import _recommended_bioemu_max_attempted_structures
from .pipeline import _recommended_bioemu_num_samples
from .pipeline import _resolve_af2_model_preset
from .pipeline import _split_multichain_sequence
from .pipeline import _target_record_from_pdb
from .pipeline import _tier_key
from .pipeline import _write_text
from .preflight import preflight_request
from .router import request_from_prompt
from .router import plan_from_prompt
from .storage import ensure_dir
from .storage import init_run
from .storage import RunPaths
from .storage import list_run_events
from .storage import list_runs
from .storage import new_run_id
from .storage import normalize_run_id
from .storage import load_status
from .storage import list_artifacts
from .storage import load_workflow_session
from .storage import read_artifact
from .storage import save_workflow_session
from .storage import delete_run
from .storage import append_run_event
from .storage import read_json
from .storage import resolve_run_path
from .storage import mark_cancel_requested
from .report_scoring import compute_score
from .report_scoring import scoring_config
from .model_providers import model_provider_store_from_env
from .model_providers import build_provider_summary
from .runpod_admin import build_runpod_admin_service
from .runpod_admin import sanitize_runpod_endpoint_patch
from .storage import set_status
from .storage import write_json


from .queue_eta import estimate_run_eta
from .queue_eta import estimate_stage_eta
from .chat_providers import ChatProviderError, list_chat_models, _normalize_provider
from .chat_agent import run_chat_turn
from .chat_attachments import (
    list_chat_attachments, primary_target_text, save_chat_attachments,
    session_attachment_context)
from .queue_stats import QueueStatsStore
from .runpod_metrics import get_runpod_metrics_store
from .runpod_metrics import latest_health
from .config import load_config as _load_config_for_eta


def compute_queue_eta(*, output_root, store, remaining_stages: list[dict]) -> dict:
    """Combine live endpoint health with EWMA durations into a run ETA.

    ``remaining_stages`` is a list of ``{"stage", "endpoint_id"}``. For each,
    read latest health (queued/running/workers) and average duration, then
    estimate wait/finish. Missing duration -> that stage is a counts-only
    fallback. Returns the ``estimate_run_eta`` shape (per_stage + run summary).
    """
    stats = QueueStatsStore(output_root)
    per_stage: list[dict] = []
    for st in remaining_stages:
        eid = st["endpoint_id"]
        h = latest_health(store, eid)
        est = estimate_stage_eta(
            jobs_ahead=(h["queued"] if h else 0),
            workers=(h["workers"] if h else 0),
            avg_duration_s=stats.avg_duration(eid),
        )
        per_stage.append(
            {
                "stage": st["stage"],
                "endpoint_id": eid,
                "queued": (h["queued"] if h else None),
                "running": (h["running"] if h else None),
                **est,
            }
        )
    return estimate_run_eta(per_stage)


def _queue_eta_tool(runner, arguments: dict) -> dict:
    """MCP handler for pipeline.queue_eta.

    Resolves the run's remaining pipeline stages (from its status) to RunPod
    endpoints, then returns an approximate per-stage + whole-run ETA. With no
    run_id (or an unknown stage) it reports all RunPod-backed stages.
    """
    from .pipeline import _PIPELINE_STAGE_ORDER  # deferred: avoid import cycle

    run_id = str((arguments or {}).get("run_id") or "").strip()
    rp = _load_config_for_eta().runpod
    stage_ep = {
        "msa": rp.mmseqs_endpoint_id,
        "rfd3": rp.rfd3_endpoint_id,
        "bioemu": rp.bioemu_endpoint_id,
        "design": rp.proteinmpnn_endpoint_id,
        "af2": rp.alphafold2_endpoint_id or rp.colabfold_endpoint_id,
        "novelty": rp.mmseqs_endpoint_id,
    }
    current = None
    if run_id:
        status = load_status(runner.output_root, run_id) or {}
        current = str(status.get("stage") or "").strip().lower() or None
    order = list(_PIPELINE_STAGE_ORDER)
    stages = order[order.index(current):] if current in order else order
    remaining = [
        {"stage": s, "endpoint_id": stage_ep[s]} for s in stages if stage_ep.get(s)
    ]
    store = get_runpod_metrics_store(runner.output_root)
    out = compute_queue_eta(
        output_root=runner.output_root, store=store, remaining_stages=remaining
    )
    out["run_id"] = run_id or None
    out["current_stage"] = current
    return out


def _is_exaone_provider(provider: str) -> bool:
    """True when the request targets the keyless local EXAONE provider."""
    try:
        return _normalize_provider(provider) == "exaone"
    except ChatProviderError:
        return False


# Sliding-window rate limit for the keyless EXAONE path only. The commercial
# providers are self-limited by the user's own API key/quota, but EXAONE is
# server-funded and unauthenticated, so we cap requests per chat session (with a
# coarse global fallback when no session id is supplied) to prevent abuse.
# Limits are read lazily from env (LOCAL_LLM_RATE_MAX requests per
# LOCAL_LLM_RATE_WINDOW seconds) since the _env_* helpers are defined below.
_exaone_rate_hits: dict[str, list[float]] = {}


def _exaone_rate_ok(session_id: str) -> bool:
    """In-process sliding-window limiter. Returns False when the caller exceeds
    the configured request budget within the window (per session, global fallback)."""
    max_hits = _env_int("LOCAL_LLM_RATE_MAX", 20)
    window = _env_float("LOCAL_LLM_RATE_WINDOW", 60.0)
    bucket = session_id or "__global__"
    now = time.monotonic()
    hits = [t for t in _exaone_rate_hits.get(bucket, []) if now - t < window]
    if len(hits) >= max_hits:
        _exaone_rate_hits[bucket] = hits
        return False
    hits.append(now)
    _exaone_rate_hits[bucket] = hits
    return True


def _chat_list_models_tool(runner, arguments: dict) -> dict:
    """MCP handler for chat.list_models. The api_key is used transiently to call
    the provider and is never stored or logged. EXAONE needs no key."""
    provider = str(arguments.get("provider") or "").strip()
    api_key = str(arguments.get("api_key") or "").strip()
    try:
        models = list_chat_models(provider, api_key)
    except ChatProviderError as exc:
        return {"error": {"kind": exc.kind, "message": exc.message}, "provider": provider}
    return {"provider": provider, "models": models}


# The chatbot exposes dot-free tool names to LLM providers (Anthropic/OpenAI
# reject "." in tool names with a 400). Map each wire name back to its real,
# dotted MCP tool name for server-side dispatch. This map is also the allowlist:
# a name not present here is refused without dispatch.
_CHAT_TOOL_MAP = {
    "pipeline_status": "pipeline.status",
    "pipeline_queue_eta": "pipeline.queue_eta",
    "pipeline_list_runs": "pipeline.list_runs",
    "pipeline_list_artifacts": "pipeline.list_artifacts",
}


def _build_chat_system_prompt(context: dict) -> str:
    tab = str((context or {}).get("tab") or "").strip() or "unknown"
    run_id = str((context or {}).get("run_id") or "").strip()
    lang = str((context or {}).get("lang") or "").strip().lower()
    is_ko = lang.startswith("ko")
    reply_lang = "Korean" if is_ko else "English"
    run_label = "Fast 실행" if is_ko else "Run Fast"
    lines = [
        "You are the RAPID protein-design assistant embedded in the web app.",
        "Help the user understand run state and results, and guide them to the right page.",
        "You can read run state with the provided tools (status, queue_eta, list_runs, list_artifacts).",
        "There are two launch pages: 'fast' runs with standard defaults and needs only a target "
        "(no other parameters); 'advanced' is the step-by-step page where the user inputs and adjusts "
        "parameters (Input -> Workflow -> Criteria -> Advanced options -> Review).",
        "Route by intent: if the user just wants to run with defaults, navigate to 'fast'. If the user "
        "wants to INPUT/SET/ADJUST parameters, asks how to configure, or asks about parameters, navigate "
        "to 'advanced' (NOT fast) and explain the step order above.",
        "You never start runs yourself — the user launches from the page. Do not claim a run has started.",
        "If the user attached a file, include prefill {\"attachment\": \"<the attached file name>\"} in the "
        "navigate call so the file is pre-loaded as the target on whichever page you choose (fast or advanced).",
        f"When you navigate to 'fast', tell the user: the file is loaded as the target (the paste box is "
        f"opened to verify), Fast uses standard defaults, and they click the '{run_label}' button to start.",
        "When you navigate to 'advanced', tell the user their target is carried over and to fill the "
        "parameters in the Input/Workflow/Criteria/Advanced-options steps, then Review and launch.",
        "If the user asks you to recommend or fill parameters: IMMEDIATELY call configure_advanced with "
        "concrete numeric/boolean values for the core params (num_seq_per_tier, bioemu_use, "
        "bioemu_num_samples, surrogate_triage_enabled); include prefill {\"attachment\": name} when a file "
        "is attached. Do NOT just promise to fill them, do NOT ask for more info first, and do NOT reply "
        "with only 'I'll fill it in'. In the SAME message as the tool call, WRITE OUT the explanation: "
        "list each parameter you set with its exact value and one sentence on WHY (tie it to the target "
        "and the user's goal; if the goal is unknown, use sensible defaults and say so), and note any key "
        "parameters left at their defaults. Then tell the user the values are pre-filled on the Advanced "
        "page to review and that they click the run button to start. You never start the run yourself.",
        "When the user explicitly asks to START or run the pipeline (e.g. 'run it', '실행해줘', '돌려줘'), "
        "call run_pipeline (after configure_advanced if parameters still need setting in the same turn). "
        "This shows a one-click Run button in the chat; tell the user to press the '▶ 지금 실행 / Run now' "
        "button. You never start the run yourself.",
        "When the user asks about the attached target, use the attached file content and summary "
        "provided in the conversation (title, chains, ligands, sequence) to describe it directly — do "
        "not say you cannot open the file. You cannot browse external websites; if the user wants an "
        "RCSB lookup by PDB ID, give them the URL https://www.rcsb.org/structure/<ID>.",
        f"Reply in {reply_lang}, matching the current UI language. Refer to buttons/labels in {reply_lang}.",
        f"Current page tab: {tab}. UI language: {reply_lang}.",
    ]
    if run_id:
        lines.append(f"Currently selected run_id: {run_id}.")
    return "\n".join(lines)


def _chat_send_tool(runner, arguments: dict) -> dict:
    """MCP handler for chat.send. Runs the agent loop; read tools execute server-side
    through an allowlisted dispatcher. The api_key is transient and never stored/logged."""
    provider = str(arguments.get("provider") or "").strip()
    model = str(arguments.get("model") or "").strip()
    api_key = str(arguments.get("api_key") or "").strip()
    messages = list(arguments.get("messages") or [])
    context = arguments.get("context") or {}
    session_id = str(arguments.get("session_id") or "").strip()
    attachments = arguments.get("attachments") or []
    system = _build_chat_system_prompt(context)

    # Rate-limit the keyless EXAONE path (server-funded, unauthenticated). Return
    # the same error shape used for provider errors so the frontend renders it.
    if _is_exaone_provider(provider) and not _exaone_rate_ok(session_id):
        return {"error": {"kind": "upstream",
                          "message": "Too many requests to the local assistant. "
                                     "Please wait a moment and try again."},
                "provider": provider, "saved": []}

    saved = []
    if attachments and session_id:
        saved = save_chat_attachments(runner.output_root, session_id, attachments)
    # Inject the content/summary of ALL files saved this session (not just this
    # turn) so the assistant can describe the attached target on any follow-up.
    if session_id:
        ctx = session_attachment_context(runner.output_root, session_id)
        if ctx:
            messages = [dict(m) for m in messages]
            for m in reversed(messages):
                if m.get("role") == "user":
                    m["content"] = f"{m.get('content') or ''}\n\n{ctx}"
                    break

    dispatcher = ToolDispatcher(runner)

    def tool_executor(name, args):
        real = _CHAT_TOOL_MAP.get(name)
        if not real:
            return {"error": "tool not available"}
        try:
            return dispatcher.call_tool(real, args or {})
        except Exception as exc:  # never leak a stack to the model
            return {"error": str(exc)}

    try:
        out = run_chat_turn(provider, model, api_key, messages, tool_executor, system=system)
    except ChatProviderError as exc:
        return {"error": {"kind": exc.kind, "message": exc.message},
                "provider": provider, "saved": saved}
    acts = out.get("actions", [])
    # Attach the saved target's content to configure/navigate actions so the
    # frontend can pre-fill the target even if the browser lost the upload
    # (e.g. after a hard refresh). Resolved once from server-side storage.
    if acts and session_id:
        tgt = primary_target_text(runner.output_root, session_id)
        if tgt:
            for a in acts:
                if isinstance(a, dict) and a.get("type") in ("configure", "navigate"):
                    a["target_text"] = tgt
    return {"provider": provider, "model": model,
            "reply": out.get("reply", ""), "actions": acts, "saved": saved}


def _chat_list_attachments_tool(runner, arguments: dict) -> dict:
    session_id = str(arguments.get("session_id") or "").strip()
    if not session_id:
        return {"attachments": []}
    return {"attachments": list_chat_attachments(runner.output_root, session_id)}


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("value", "text", "content", "data"):
            if key in value:
                return _as_text(value.get(key))
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            return "\n".join(value)
        if all(isinstance(v, (bytes, bytearray)) for v in value):
            return b"\n".join(bytes(v) for v in value).decode("utf-8", errors="replace")
    return str(value)


def _canonical_pipeline_stage_arg(value: object | None) -> str | None:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return None
    if raw in {"wt_diff", "wtdiff"}:
        return "novelty"
    return raw


_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_id(value: str) -> str:
    safe = _SAFE_ID_RE.sub("_", str(value or "")).strip("._-")
    return safe[:128] or "id"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_json(obj: object) -> object:
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


def _as_dict(value: object | None, *, name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _as_dict_str(value: object | None, *, name: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    out: dict[str, str] = {}
    for k, v in value.items():
        if v is None:
            continue
        out[str(k)] = _as_text(v)
    return out or None


def _as_str_or_list(value: object | None) -> str | list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return str(value)


def _as_rfd3_select_fixed_atoms(
    value: object | None,
) -> str | list[str] | dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for key, item in value.items():
            if item is None:
                continue
            out[str(key)] = str(item)
        return out or None
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            out: dict[str, str] = {}
            for key, item in parsed.items():
                if item is None:
                    continue
                out[str(key)] = str(item)
            return out or None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
    return text


def _as_list_of_str(value: object | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            out.append(str(item))
        return out
    return [str(value)]


def _as_model_name_selection(
    value: object | None, *, default: str | list[str] = "rf"
) -> str | list[str]:
    if value is None:
        return default
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(
                part.strip()
                for part in re.split(r"[,;\n]+", _as_text(item))
                if part.strip()
            )
        return items or default
    text = _as_text(value).strip()
    if not text:
        return default
    parts = [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]
    if len(parts) > 1:
        return parts
    return parts[0] if parts else default


def _as_list_of_float(value: object | None) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, list):
        out: list[float] = []
        for item in value:
            if isinstance(item, (int, float)):
                out.append(float(item))
            elif isinstance(item, str) and item.strip():
                out.append(float(item.strip()))
        return out
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str) and value.strip():
        return [float(value.strip())]
    return None


def _as_fixed_positions_extra(value: object | None) -> dict[str, list[int]] | None:
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        positions = sorted(
            {int(str(item).strip()) for item in value if item is not None}
        )
        positions = [pos for pos in positions if pos > 0]
        return {"*": positions} if positions else None
    if not isinstance(value, dict):
        raise ValueError("fixed_positions_extra must be an object (e.g. {'A':[1,2,3]})")

    out: dict[str, list[int]] = {}
    for raw_chain, raw_positions in value.items():
        chain = str(raw_chain)
        if raw_positions is None:
            continue
        positions_raw = (
            raw_positions if isinstance(raw_positions, list) else [raw_positions]
        )
        positions = sorted(
            {int(str(item).strip()) for item in positions_raw if item is not None}
        )
        positions = [pos for pos in positions if pos > 0]
        if positions:
            out[chain] = positions
    return out or None


def _as_bool(value: object | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _as_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(float(value.strip()))
    return default


def _as_float(value: object | None, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value.strip())
    return default


def _as_af2_provider(value: object | None, default: str = "colabfold") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(default or "colabfold").strip().lower()
    if raw in {"colabfold", "cf"}:
        return "colabfold"
    if raw in {"af2", "alphafold", "alphafold2"}:
        return "af2"
    raise ValueError("af2_provider must be one of: colabfold, af2")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_reason_list(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
        return [p for p in parts if p]
    return [str(value).strip()]


def _as_metrics(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError("metrics must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("metrics must be a JSON object")
        return {str(k): v for k, v in parsed.items()}
    return None


def _normalize_user(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key in ("id", "username", "role", "email", "org"):
            if key in value and value[key] is not None:
                out[key] = value[key]
        if out:
            return out
    if isinstance(value, str) and value.strip():
        return {"username": value.strip()}
    return None


def _parse_fasta_or_sequence(text: str) -> list[FastaRecord]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("target_fasta is required")
    if raw.lstrip().startswith(">"):
        return parse_fasta(raw)
    seq = "".join(ch for ch in raw.split())
    if not seq:
        raise ValueError("target_fasta is empty")
    return [FastaRecord(header="sequence", sequence=seq)]


def _prepared_chain_count(prepared_sequence: str) -> int:
    """Number of chains in a worker-ready AF2 sequence."""
    return str(prepared_sequence or "").count(_AF2_MULTIMER_CHAIN_DELIMITER) + 1


def _multimer_output_chain_error(
    pdb_text: str, *, model_preset: str, expected_chains: int
) -> str | None:
    """Reject a multimer prediction that came back as one fused chain.

    A complex whose ranked_0.pdb carries fewer chains than were requested is
    not a complex: the worker folded the chains as a single polypeptide.
    Reporting that as a completed run is exactly how a chain-delimiter
    regression stays invisible behind a high pLDDT.
    """
    if expected_chains < 2:
        return None
    if not str(model_preset or "").strip().lower().startswith("multimer"):
        return None
    found = len(residues_by_chain(normalize_structure_text(pdb_text)))
    if found >= expected_chains:
        return None
    return (
        f"multimer output validation failed: predicted structure has {found} chain(s) "
        f"but {expected_chains} were requested. The worker folded the chains as one "
        "fused polypeptide, so this structure is not a complex."
    )


def _af2_records_from_inputs(
    *,
    target_fasta: str,
    target_pdb: str,
    model_preset: str,
    chain_ids: list[str] | None = None,
    provider: str = "colabfold",
) -> tuple[list[SequenceRecord], str]:
    if target_fasta.strip():
        fasta_records = _parse_fasta_or_sequence(target_fasta)
    elif target_pdb.strip():
        fasta_records = [_target_record_from_pdb(target_pdb, design_chains=None)]
    else:
        raise ValueError("One of target_fasta or target_pdb is required")

    chain_counts = [
        len(_split_multichain_sequence(rec.sequence)) for rec in fasta_records
    ]
    max_chains = max(chain_counts) if chain_counts else 1
    resolved_preset = _resolve_af2_model_preset(model_preset, chain_count=max_chains)

    seq_records: list[SequenceRecord] = []
    for rec in fasta_records:
        prepared = _prepare_af2_sequence(
            rec.sequence,
            model_preset=resolved_preset,
            chain_ids=chain_ids,
            provider=provider,
            chain_ids_param="af2_chain_ids",
        )
        seq_records.append(
            SequenceRecord(id=rec.id, sequence=prepared, header=rec.header, meta={})
        )
    return seq_records, resolved_preset


def _af2_provider_label(provider: str) -> str:
    return "ColabFold" if provider == "colabfold" else "AlphaFold2"


def _select_af2_client(
    runner: PipelineRunner, provider: str
) -> tuple[object | None, str]:
    requested = _normalize_af2_provider(provider)
    if requested == "colabfold":
        if runner.colabfold is not None:
            return runner.colabfold, "colabfold"
        if runner.af2 is not None:
            # Backward-compatible fallback for older deployments without ColabFold endpoint.
            return runner.af2, "af2"
        return None, "colabfold"
    if runner.af2 is not None:
        return runner.af2, "af2"
    return None, "af2"


@dataclass(frozen=True)
class AutoRetryConfig:
    enabled: bool
    max_attempts: int
    backoff_s: float


def _auto_retry_config(args: dict[str, Any]) -> AutoRetryConfig:
    enabled = _as_bool(args.get("auto_retry"), _env_true("PIPELINE_AUTO_RETRY"))
    max_attempts = _as_int(
        args.get("auto_retry_max"), _env_int("PIPELINE_AUTO_RETRY_MAX", 2)
    )
    backoff_s = _as_float(
        args.get("auto_retry_backoff_s"),
        _env_float("PIPELINE_AUTO_RETRY_BACKOFF_S", 10.0),
    )
    if not enabled:
        return AutoRetryConfig(enabled=False, max_attempts=1, backoff_s=0.0)
    return AutoRetryConfig(
        enabled=True, max_attempts=max(1, max_attempts), backoff_s=max(0.0, backoff_s)
    )


def _retry_request(
    request: PipelineRequest, error: str
) -> tuple[PipelineRequest, str] | None:
    msg = error.lower()
    if "cancelled" in msg or "canceled" in msg or "cancel requested" in msg:
        return None

    if "persistent db" in msg and "not found" in msg:
        if request.mmseqs_target_db.lower() != "uniref90":
            return (
                replace(request, mmseqs_target_db="uniref90", force=True),
                "fallback mmseqs_target_db=uniref90",
            )

    if (
        "unable to extract protein sequence from target_pdb" in msg
        and request.design_chains
    ):
        return (
            replace(request, design_chains=None, force=True),
            "retry without design_chains",
        )

    if "timed out" in msg or "timeout" in msg or "timed_out" in msg:
        if request.mmseqs_max_seqs > 100:
            new_max = max(50, int(request.mmseqs_max_seqs / 2))
            return (
                replace(request, mmseqs_max_seqs=new_max, force=True),
                f"reduce mmseqs_max_seqs to {new_max}",
            )

    if "runpod job not completed" in msg or "mmseqs error" in msg or "a3m" in msg:
        return (replace(request, force=True), "retry runpod job")

    return None


def _run_with_auto_retry(
    runner: PipelineRunner,
    request: PipelineRequest,
    *,
    run_id: str | None,
    retry: AutoRetryConfig,
) -> PipelineResult:
    attempt = 1
    while True:
        try:
            return runner.run(request, run_id=run_id)
        except PipelineCancelled:
            raise
        except Exception as exc:
            if not retry.enabled or attempt >= retry.max_attempts:
                raise
            decision = _retry_request(request, str(exc))
            if decision is None:
                raise
            request, _ = decision
            if retry.backoff_s > 0:
                time.sleep(retry.backoff_s)
            attempt += 1


def _run_af2_predict(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    target_fasta = _as_text(arguments.get("target_fasta"))
    target_pdb = _as_text(resolve_structure_input(arguments.get("target_pdb")))
    dry_run = _as_bool(arguments.get("dry_run"), False)
    requested_provider = _as_af2_provider(arguments.get("af2_provider"), "colabfold")
    af2_client, effective_provider = _select_af2_client(runner, requested_provider)
    provider_label = _af2_provider_label(effective_provider)

    requested_preset = str(arguments.get("af2_model_preset") or "auto")
    db_preset = str(arguments.get("af2_db_preset") or "full_dbs")
    max_template_date = str(arguments.get("af2_max_template_date") or "2020-05-14")
    extra_flags = (
        str(arguments.get("af2_extra_flags"))
        if arguments.get("af2_extra_flags")
        else None
    )
    # Per-sequence iteration is the safe default for multi-record FASTA: a
    # single worker 5xx on one sequence no longer kills the whole batch.
    # Callers that want a true batch request can pass af2_batch_size>1.
    af2_batch_size = max(1, _as_int(arguments.get("af2_batch_size"), 1))
    auto_recover = _as_bool(arguments.get("auto_recover"), True)
    af2_chain_ids = _as_list_of_str(arguments.get("af2_chain_ids"))
    sequence_id = str(arguments.get("sequence_id") or "").strip() or None

    normalized_run_id = (
        normalize_run_id(str(run_id)) if run_id is not None else new_run_id("af2")
    )
    paths = init_run(runner.output_root, normalized_run_id)
    set_status(paths, stage="af2", state="running")

    request_payload = {
        "target_fasta": target_fasta,
        "target_pdb": target_pdb,
        "af2_model_preset": requested_preset,
        "af2_db_preset": db_preset,
        "af2_max_template_date": max_template_date,
        "af2_extra_flags": extra_flags,
        "af2_provider": requested_provider,
        "af2_provider_effective": effective_provider,
        "af2_chain_ids": af2_chain_ids,
        "sequence_id": sequence_id,
        "dry_run": dry_run,
    }
    write_json(paths.request_json, _safe_json(request_payload))

    af2_dir = ensure_dir(paths.root / "af2")
    jobs: dict[str, str] = {}

    def _on_job_id(seq_id: str, job_id: str) -> None:
        jobs[seq_id] = job_id
        write_json(af2_dir / "runpod_jobs.json", {"jobs": dict(jobs)})
        set_status(
            paths,
            stage="af2",
            state="running",
            detail=f"runpod_job_id={job_id} seq_id={seq_id}",
        )

    try:
        seq_records, resolved_preset = _af2_records_from_inputs(
            target_fasta=target_fasta,
            target_pdb=target_pdb,
            model_preset=requested_preset,
            chain_ids=af2_chain_ids,
            provider=effective_provider,
        )

        if dry_run:

            def _preview_chains(seq: str) -> list[str]:
                parts = [
                    "".join(ch for ch in part if ch.isalpha())
                    for part in str(seq or "").split(_AF2_MULTIMER_CHAIN_DELIMITER)
                ]
                return [part for part in parts if part] or ["A"]

            def _preview_chain_id(index: int) -> str:
                if af2_chain_ids and index < len(af2_chain_ids):
                    label = str(af2_chain_ids[index]).strip()
                    if label:
                        return label[0]
                return chr(ord("A") + index) if index < 26 else "Z"

            results = {}
            for rec in seq_records:
                # Preview every chain, not just the first: a one-chain preview
                # of a multimer is what a fused prediction looks like.
                chains = _preview_chains(rec.sequence)
                body: list[str] = []
                for index, chain_seq in enumerate(chains):
                    chain_pdb = _dummy_backbone_pdb(
                        chain_seq, chain_id=_preview_chain_id(index)
                    )
                    body.extend(
                        line
                        for line in chain_pdb.splitlines()
                        if line.strip() and line.strip() != "END"
                    )
                body.append("END")
                results[rec.id] = {
                    "best_plddt": 90.0,
                    "best_model": None,
                    "ranking_debug": {},
                    "ranked_0_pdb": "\n".join(body) + "\n",
                }
            chunk_failures: dict[str, str] = {}
        else:
            if af2_client is None:
                raise RuntimeError(
                    "ColabFold/AlphaFold2 is not configured. "
                    "Set COLABFOLD_ENDPOINT_ID (default provider) or ALPHAFOLD2_ENDPOINT_ID/AF2_URL."
                )

            results: dict[str, Any] = {}
            chunk_failures: dict[str, str] = {}
            total = len(seq_records)
            for chunk_start in range(0, total, af2_batch_size):
                chunk = seq_records[chunk_start : chunk_start + af2_batch_size]
                set_status(
                    paths,
                    stage="af2",
                    state="running",
                    detail=f"[{chunk_start + 1}/{total}] {chunk[0].id}"
                    + (f"+{len(chunk) - 1}" if len(chunk) > 1 else ""),
                )
                try:
                    try:
                        chunk_results = af2_client.predict(
                            chunk,
                            model_preset=resolved_preset,
                            db_preset=db_preset,
                            max_template_date=max_template_date,
                            extra_flags=extra_flags,
                            on_job_id=_on_job_id,
                        )
                    except TypeError:
                        chunk_results = af2_client.predict(
                            chunk,
                            model_preset=resolved_preset,
                            db_preset=db_preset,
                            max_template_date=max_template_date,
                            extra_flags=extra_flags,
                        )
                except Exception as exc:
                    msg = str(exc)
                    recoverable = (
                        af2_error_is_server_failure(msg)
                        or af2_error_is_missing_pdb_outputs(msg)
                        or "executiontimeout" in msg.lower()
                    )
                    if auto_recover and recoverable:
                        for rec in chunk:
                            chunk_failures[rec.id] = msg
                        continue
                    raise
                if not isinstance(chunk_results, dict):
                    raise RuntimeError(
                        f"{provider_label} output invalid: {type(chunk_results).__name__}"
                    )
                results.update(chunk_results)

        if not isinstance(results, dict):
            raise RuntimeError(
                f"{provider_label} output invalid: {type(results).__name__}"
            )

        summary_results: dict[str, dict[str, Any]] = {}
        for rec in seq_records:
            if rec.id in chunk_failures:
                # Record the per-sequence failure but keep iterating.
                seq_dir = ensure_dir(af2_dir / _safe_id(rec.id))
                write_json(
                    seq_dir / "error.json",
                    {"error": chunk_failures[rec.id], "provider": effective_provider},
                )
                continue
            payload = results.get(rec.id)
            if not isinstance(payload, dict):
                if auto_recover:
                    chunk_failures[rec.id] = "predictor returned no record"
                    seq_dir = ensure_dir(af2_dir / _safe_id(rec.id))
                    write_json(
                        seq_dir / "error.json",
                        {"error": "predictor returned no record", "provider": effective_provider},
                    )
                    continue
                raise RuntimeError(
                    f"{provider_label} output missing record for {rec.id}"
                )

            ranked0 = (
                payload.get("ranked_0_pdb")
                or payload.get("pdb")
                or payload.get("pdb_text")
            )
            if not isinstance(ranked0, str) or not ranked0.strip():
                if auto_recover:
                    chunk_failures[rec.id] = "missing ranked_0.pdb"
                    seq_dir = ensure_dir(af2_dir / _safe_id(rec.id))
                    write_json(
                        seq_dir / "error.json",
                        {"error": "missing ranked_0.pdb", "provider": effective_provider},
                    )
                    continue
                raise RuntimeError(
                    f"{provider_label} output missing ranked_0.pdb for {rec.id}"
                )

            seq_dir = ensure_dir(af2_dir / _safe_id(rec.id))
            _write_text(seq_dir / "ranked_0.pdb", ranked0)
            chain_error = _multimer_output_chain_error(
                ranked0,
                model_preset=resolved_preset,
                expected_chains=_prepared_chain_count(rec.sequence),
            )
            if chain_error:
                if not auto_recover:
                    raise RuntimeError(f"{provider_label}: {chain_error}")
                chunk_failures[rec.id] = chain_error
                write_json(
                    seq_dir / "error.json",
                    {"error": chain_error, "provider": effective_provider},
                )
                continue
            if isinstance(payload.get("ranking_debug"), dict):
                write_json(seq_dir / "ranking_debug.json", payload["ranking_debug"])
            write_json(
                seq_dir / "metrics.json",
                {
                    "best_plddt": payload.get("best_plddt"),
                    "best_model": payload.get("best_model"),
                    "archive_name": payload.get("archive_name"),
                    "provider": effective_provider,
                },
            )
            summary_results[rec.id] = {
                "best_plddt": payload.get("best_plddt"),
                "best_model": payload.get("best_model"),
            }

        write_json(af2_dir / "results.json", _safe_json(results))
        summary = {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "af2": summary_results,
            "af2_model_preset": resolved_preset,
            "af2_provider": effective_provider,
            "af2_provider_requested": requested_provider,
            "completed_count": len(summary_results),
            "failed_count": len(chunk_failures),
            "total_count": len(seq_records),
            "failures": chunk_failures,
            "af2_batch_size": af2_batch_size,
            "auto_recover": auto_recover,
        }
        write_json(paths.summary_json, _safe_json(summary))
        done_detail = (
            f"completed={len(summary_results)}/{len(seq_records)} failed={len(chunk_failures)}"
            if chunk_failures
            else None
        )
        set_status(paths, stage="done", state="completed", detail=done_detail)
        return {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "summary": summary,
        }
    except Exception as exc:
        set_status(paths, stage="error", state="failed", detail=str(exc))
        error_summary = {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "errors": [str(exc)],
        }
        write_json(paths.summary_json, _safe_json(error_summary))
        raise


def _run_diffdock(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    protein_pdb = _as_text(resolve_structure_input(arguments.get("protein_pdb"))) or _as_text(
        resolve_structure_input(arguments.get("target_pdb"))
    )
    ligand_smiles = _as_text(arguments.get("diffdock_ligand_smiles")) or _as_text(
        arguments.get("ligand_smiles")
    )
    ligand_sdf = _as_text(arguments.get("diffdock_ligand_sdf")) or _as_text(
        arguments.get("ligand_sdf")
    )
    complex_name = str(arguments.get("complex_name") or "complex")
    diffdock_config = str(
        arguments.get("diffdock_config") or "default_inference_args.yaml"
    )
    diffdock_extra_args = _as_text(arguments.get("diffdock_extra_args")).strip() or None
    diffdock_cuda_visible_devices = (
        _as_text(arguments.get("diffdock_cuda_visible_devices")).strip() or None
    )
    dry_run = _as_bool(arguments.get("dry_run"), False)

    if not protein_pdb.strip():
        raise ValueError("protein_pdb is required")
    if not (ligand_smiles.strip() or ligand_sdf.strip()):
        raise ValueError("diffdock_ligand_smiles or diffdock_ligand_sdf is required")
    ligand_smiles, ligand_sdf = normalize_diffdock_ligand_inputs(
        ligand_smiles, ligand_sdf
    )
    ligand_smiles_text = ligand_smiles or ""
    ligand_sdf_text = ligand_sdf or ""

    normalized_run_id = (
        normalize_run_id(str(run_id)) if run_id is not None else new_run_id("diffdock")
    )
    paths = init_run(runner.output_root, normalized_run_id)
    set_status(paths, stage="diffdock", state="running")

    request_payload = {
        "protein_pdb": protein_pdb,
        "diffdock_ligand_smiles": ligand_smiles_text or None,
        "diffdock_ligand_sdf": ligand_sdf_text or None,
        "complex_name": complex_name,
        "diffdock_config": diffdock_config,
        "diffdock_extra_args": diffdock_extra_args,
        "diffdock_cuda_visible_devices": diffdock_cuda_visible_devices,
        "dry_run": dry_run,
    }
    write_json(paths.request_json, _safe_json(request_payload))

    diffdock_dir = ensure_dir(paths.root / "diffdock")
    _write_text(diffdock_dir / "protein.pdb", protein_pdb)
    if ligand_sdf_text.strip():
        _write_text(diffdock_dir / "ligand.sdf", ligand_sdf_text)
    else:
        _write_text(diffdock_dir / "ligand.smiles", ligand_smiles_text)

    def _on_job_id(job_id: str) -> None:
        write_json(diffdock_dir / "runpod_job.json", {"job_id": job_id})
        set_status(
            paths, stage="diffdock", state="running", detail=f"runpod_job_id={job_id}"
        )

    try:
        if dry_run:
            output_payload = {"dry_run": True}
            sdf_text = ligand_sdf_text if ligand_sdf_text.strip() else ""
        else:
            if runner.diffdock is None:
                raise RuntimeError(
                    "DiffDock endpoint is not configured (set DIFFDOCK_ENDPOINT_ID)"
                )
            diffdock_out = runner.diffdock.dock(
                protein_pdb=protein_pdb,
                ligand_smiles=ligand_smiles_text or None,
                ligand_sdf=ligand_sdf_text or None,
                complex_name=complex_name,
                config=diffdock_config,
                extra_args=diffdock_extra_args,
                cuda_visible_devices=diffdock_cuda_visible_devices,
                on_job_id=_on_job_id,
            )
            output_payload = diffdock_out.get("output") or {}
            zip_bytes = diffdock_out.get("zip_bytes")
            if isinstance(zip_bytes, (bytes, bytearray)):
                (diffdock_dir / "out_dir.zip").write_bytes(bytes(zip_bytes))
            sdf_text = str(diffdock_out.get("sdf_text") or "")

        write_json(diffdock_dir / "output.json", _safe_json(output_payload))
        if sdf_text.strip():
            _write_text(diffdock_dir / "rank1.sdf", sdf_text)
            ligand_pdb = sdf_to_pdb(sdf_text)
            _write_text(diffdock_dir / "ligand.pdb", ligand_pdb)
            complex_pdb = append_ligand_pdb(protein_pdb, ligand_pdb)
            _write_text(diffdock_dir / "complex.pdb", complex_pdb)

        summary = {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "diffdock_dir": str(diffdock_dir),
        }
        write_json(paths.summary_json, _safe_json(summary))
        set_status(paths, stage="done", state="completed")
        return {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "summary": summary,
        }
    except Exception as exc:
        set_status(paths, stage="error", state="failed", detail=str(exc))
        error_summary = {
            "run_id": normalized_run_id,
            "output_dir": str(paths.root),
            "errors": [str(exc)],
        }
        write_json(paths.summary_json, _safe_json(error_summary))
        raise


def _delete_run_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id is required")
    force = _as_bool(arguments.get("force"), False)
    status = load_status(runner.output_root, run_id)
    if (
        status is not None
        and str(status.get("state") or "").lower() == "running"
        and not force
    ):
        raise ValueError(
            "run is still running; stop it or set force=true to delete anyway"
        )
    res = delete_run(runner.output_root, run_id)
    for path in _projects_root(runner.output_root).glob("*/rounds/*.json"):
        record = _load_json_record(path)
        if record and "linked_run_ids" in record and run_id in record["linked_run_ids"]:
            record["linked_run_ids"] = [
                r for r in record["linked_run_ids"] if r != run_id
            ]
            write_json(path, record)
    return res


def _cancel_run_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id is required")
    root = resolve_run_path(runner.output_root, run_id)
    if not root.exists():
        return {"run_id": run_id, "found": False, "cancelled": 0, "jobs": []}

    mark_cancel_requested(runner.output_root, run_id, reason="pipeline.cancel_run")

    jobs = _collect_runpod_jobs(root)
    af2_clients = [
        client for client in (runner.colabfold, runner.af2) if client is not None
    ]
    af2_runpod = None
    for client in af2_clients:
        info = _client_cancel_info(client)
        if info is not None:
            af2_runpod = info[0]
            break
    client_map = {
        "mmseqs": [runner.mmseqs],
        "proteinmpnn": [runner.proteinmpnn],
        "rfd3": [runner.rfd3],
        "diffdock": [runner.diffdock],
        "af2": af2_clients,
    }
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, object]] = []
    errors: list[str] = []
    cancelled = 0

    for job in jobs:
        kind = str(job.get("kind") or "unknown")
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        key = (kind, job_id)
        if key in seen:
            continue
        seen.add(key)

        explicit_endpoint_id = str(job.get("endpoint_id") or "").strip()
        if explicit_endpoint_id and af2_runpod is not None and kind == "af2":
            try:
                resp = af2_runpod.cancel(explicit_endpoint_id, job_id)
                status = None
                if isinstance(resp, dict):
                    status = resp.get("status") or resp.get("state")
                results.append(
                    {
                        "kind": kind,
                        "job_id": job_id,
                        "endpoint_id": explicit_endpoint_id,
                        "status": status or "cancel_requested",
                    }
                )
                cancelled += 1
                continue
            except Exception as exc:
                msg = f"{kind}:{job_id}: {exc}"
                errors.append(msg)
                results.append(
                    {
                        "kind": kind,
                        "job_id": job_id,
                        "endpoint_id": explicit_endpoint_id,
                        "error": str(exc),
                    }
                )
                continue

        clients = [client for client in client_map.get(kind, []) if client is not None]
        if not clients:
            results.append(
                {
                    "kind": kind,
                    "job_id": job_id,
                    "status": "skipped",
                    "reason": "endpoint_not_configured",
                }
            )
            continue

        attempt_errors: list[str] = []
        cancelled_this_job = False
        for client in clients:
            cancel_info = _client_cancel_info(client)
            if cancel_info is None:
                continue
            runpod, endpoint_id = cancel_info
            try:
                resp = runpod.cancel(endpoint_id, job_id)
                status = None
                if isinstance(resp, dict):
                    status = resp.get("status") or resp.get("state")
                results.append(
                    {
                        "kind": kind,
                        "job_id": job_id,
                        "endpoint_id": endpoint_id,
                        "status": status or "cancel_requested",
                    }
                )
                cancelled += 1
                cancelled_this_job = True
                break
            except Exception as exc:
                attempt_errors.append(f"{endpoint_id}: {exc}")

        if not cancelled_this_job:
            reason = (
                "; ".join(attempt_errors)
                if attempt_errors
                else "endpoint_not_configured"
            )
            msg = f"{kind}:{job_id}: {reason}"
            errors.append(msg)
            results.append({"kind": kind, "job_id": job_id, "error": reason})

    status = load_status(runner.output_root, run_id)
    stage = (
        str(status.get("stage") or "cancel") if isinstance(status, dict) else "cancel"
    )
    paths = RunPaths(run_id=run_id, root=root)
    detail = f"cancelled_jobs={cancelled}" if cancelled else "cancel_requested"
    set_status(paths, stage=stage, state="cancelled", detail=detail)

    return {
        "run_id": run_id,
        "found": True,
        "cancelled": cancelled,
        "errors": errors,
        "jobs": results,
    }


def _submit_feedback(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")

    rating = str(arguments.get("rating") or "").strip().lower()
    if rating not in {"good", "bad"}:
        raise ValueError("rating must be 'good' or 'bad'")

    reasons = _as_reason_list(arguments.get("reasons"))
    comment = _as_text(arguments.get("comment")).strip() or None
    artifact_path = _as_text(arguments.get("artifact_path")).strip() or None
    stage = _as_text(arguments.get("stage")).strip().lower() or None
    metrics = _as_metrics(arguments.get("metrics")) or {}
    user = _normalize_user(arguments.get("user"))

    entry: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "rating": rating,
        "reasons": reasons,
        "comment": comment,
        "artifact_path": artifact_path,
        "stage": stage,
        "metrics": metrics,
        "user": user,
        "created_at": _now_iso(),
    }
    return append_run_event(
        runner.output_root, run_id, filename="feedback.jsonl", payload=entry
    )


def _list_feedback(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    limit = _as_int(arguments.get("limit"), 50)
    items = list_run_events(
        runner.output_root, run_id, filename="feedback.jsonl", limit=limit
    )
    return {"run_id": run_id, "items": items}


def _submit_experiment(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")

    assay_type = str(arguments.get("assay_type") or "unspecified").strip()
    result = str(arguments.get("result") or "").strip().lower()
    if result not in {"success", "fail", "inconclusive"}:
        raise ValueError("result must be one of: success, fail, inconclusive")

    metrics = _as_metrics(arguments.get("metrics")) or {}
    conditions = _as_text(arguments.get("conditions")).strip() or None
    sample_id = _as_text(arguments.get("sample_id")).strip() or None
    candidate_id = _as_text(arguments.get("candidate_id")).strip() or None
    sequence_id = _as_text(arguments.get("sequence_id")).strip() or None
    metric_name = _as_text(arguments.get("metric_name")).strip() or None
    metric_unit = _as_text(arguments.get("metric_unit")).strip() or None
    metric_direction = (
        _as_text(arguments.get("metric_direction")).strip().lower() or None
    )
    if metric_direction and metric_direction not in {"maximize", "minimize"}:
        raise ValueError("metric_direction must be 'maximize' or 'minimize'")
    metric_value = None
    if arguments.get("metric_value") is not None:
        metric_value = _as_float(arguments.get("metric_value"), 0.0)
        if metric_name:
            metrics = dict(metrics)
            metrics.setdefault(metric_name, metric_value)
    replicate_id = _as_text(arguments.get("replicate_id")).strip() or None
    artifact_path = _as_text(arguments.get("artifact_path")).strip() or None
    note = _as_text(arguments.get("note")).strip() or None
    user = _normalize_user(arguments.get("user"))

    entry: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "assay_type": assay_type,
        "result": result,
        "metrics": metrics,
        "conditions": conditions,
        "sample_id": sample_id,
        "candidate_id": candidate_id,
        "sequence_id": sequence_id,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "metric_unit": metric_unit,
        "metric_direction": metric_direction,
        "replicate_id": replicate_id,
        "artifact_path": artifact_path,
        "note": note,
        "user": user,
        "created_at": _now_iso(),
    }
    return append_run_event(
        runner.output_root, run_id, filename="experiments.jsonl", payload=entry
    )


def _list_experiments(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    limit = _as_int(arguments.get("limit"), 50)
    items = list_run_events(
        runner.output_root, run_id, filename="experiments.jsonl", limit=limit
    )
    return {"run_id": run_id, "items": items}


def _list_agent_events(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    limit = _as_int(arguments.get("limit"), 50)
    items = list_run_events(
        runner.output_root, run_id, filename="agent_panel.jsonl", limit=limit
    )
    return {"run_id": run_id, "items": items}


def _agent_chat_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    prompt = str(arguments.get("prompt") or "").strip()
    lang = str(arguments.get("lang") or "en").lower()
    if not run_id:
        # Try to find the latest run_id if not provided
        try:
            output_path = Path(runner.output_root)
            runs = sorted(
                [
                    d.name
                    for d in output_path.iterdir()
                    if d.is_dir() and not d.name.startswith("_")
                ]
            )
            if runs:
                run_id = runs[-1]
        except Exception:
            pass
    if not run_id:
        raise ValueError("run_id is required")

    run_root = resolve_run_path(runner.output_root, run_id)
    status = read_json(run_root / "status.json") or {}
    summary = read_json(run_root / "summary.json") or {}
    agent_events = list_run_events(
        runner.output_root, run_id, filename="agent_panel.jsonl", limit=20
    )

    # Reasoning logic: Synthesize information from different stages
    is_ko = lang.startswith("ko")
    response_lines = []

    # 1. Status Awareness
    state = str(status.get("state") or "unknown")
    stage = str(status.get("stage") or "none")

    if is_ko:
        response_lines.append(
            f"현재 Run {run_id}은(는) {stage} 단계에서 {state} 상태입니다."
        )
    else:
        response_lines.append(
            f"Run {run_id} is currently in {state} state at {stage} stage."
        )

    # 2. Expert Interpretation from Agent Panel
    interpretations = []
    for event in agent_events:
        interp = event.get("consensus", {}).get("interpretations")
        if isinstance(interp, list):
            interpretations.extend(interp)

    if interpretations:
        unique_interp = list(dict.fromkeys(interpretations))  # Remove duplicates
        if is_ko:
            response_lines.append("\n### 전문가 분석 (Agent Panel Insights):")
        else:
            response_lines.append("\n### Expert Insights (Agent Panel):")
        for i in unique_interp[-5:]:  # Show last 5 unique insights
            response_lines.append(f"- {i}")

    # 3. Evolution Specific Reasoning (The new 3-stage BO)
    evo_stages = summary.get("stages")
    if evo_stages and summary.get("evolution_mode"):
        passed = evo_stages.get("stage1_passed", 0)
        total = evo_stages.get("stage1_total", 0)
        cutoff = evo_stages.get("soluprot_cutoff", 0.0)

        if is_ko:
            response_lines.append("\n### 계층적 BO 분석 (Hierarchical BO Analysis):")
            response_lines.append(
                f"- **Stage 1 (SoluProt Gate):** 총 {total}개 후보 중 {passed}개가 통과했습니다 (임계값: {cutoff})."
            )
            if total > 0 and (passed / total) < 0.2:
                response_lines.append(
                    "  - *분석:* 수용성 필터링 통과율이 매우 낮습니다. 설계 시 sampling_temp를 조절하거나 soluprot_cutoff를 낮추는 것을 추천합니다."
                )
        else:
            response_lines.append("\n### Hierarchical BO Analysis:")
            response_lines.append(
                f"- **Stage 1 (SoluProt Gate):** {passed}/{total} sequences passed the gate (Cutoff: {cutoff})."
            )
            if total > 0 and (passed / total) < 0.2:
                response_lines.append(
                    "  - *Insight:* Low solubility pass rate detected. Consider adjusting sampling_temp or lowering soluprot_cutoff."
                )

    # 4. Action Recommendation (Gemini Upgrade)
    context_text = "\n".join(response_lines)
    system_instruction = (
        "You are an AI Protein Engineering Expert integrated into a design pipeline. "
        "Your goal is to help users analyze results, troubleshoot failures, and optimize design parameters. "
        "Use the provided context (status, summary, expert interpretations) to give specific, actionable advice. "
        "Be professional, technical, and bilingual (respond in the user's language - Korean or English). "
        "If the data shows low MSA depth, suggest increasing max_seqs. If solubility pass rate is low, suggest lowering soluprot_cutoff or sampling_temp."
    )

    final_reply = ""
    model_used = "local-fallback"
    if runner.gemini and runner.gemini.is_available():
        try:
            gemini_advice = runner.gemini.chat(
                system_instruction,
                f"Pipeline Context:\n{context_text}\n\nUser Question/Prompt: {prompt}\n\nPlease provide a summary of insights and actionable advice.",
            )
            final_reply = gemini_advice
            model_used = runner.gemini.model_name
        except Exception as e:
            final_reply = f"{context_text}\n\n(Gemini Error: {e})\nConsider optimizing parameters based on the insights above."
    else:
        # Standard fallback logic
        if is_ko:
            response_lines.append("\n### 추천 액션:")
            if state == "failed":
                response_lines.append(
                    "- 로그를 분석한 결과 파라미터 최적화가 필요해 보입니다. `resume` 기능을 통해 설정을 변경하여 재시작할 수 있습니다."
                )
            else:
                response_lines.append(
                    "- 현재 결과가 만족스럽다면 `Compare Studio`에서 상위 후보들을 정밀 검토해 보세요."
                )
        else:
            response_lines.append("\n### Recommended Actions:")
            if state == "failed":
                response_lines.append(
                    "- Analysis suggests parameter tuning is needed. Use the `resume` feature to restart with adjusted settings."
                )
            else:
                response_lines.append(
                    "- If results look promising, proceed to `Compare Studio` for high-fidelity review."
                )
        final_reply = "\n".join(response_lines)

    # 5. Training Data Collection (Reasoning Dataset)
    try:
        dataset_dir = Path(runner.output_root) / "_reasoning_data"
        dataset_dir.mkdir(parents=True, exist_ok=True)

        # Monthly file rotation: dataset_2026_04.jsonl
        filename = f"dataset_{time.strftime('%Y_%m', time.gmtime())}.jsonl"
        log_entry = {
            "timestamp": _now_iso(),
            "run_id": run_id,
            "model": model_used,
            "context": context_text,
            "expert_panel_data": interpretations,
            "prompt": prompt,
            "response": final_reply,
            "language": lang,
        }

        with open(dataset_dir / filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Prevent logging failure from breaking the tool response
        pass

    return {"run_id": run_id, "reply": final_reply, "status": state, "stage": stage}


def _workspace_root(output_root: str) -> Path:
    return ensure_dir(Path(output_root).resolve() / "_workspace")


def _projects_root(output_root: str) -> Path:
    return ensure_dir(_workspace_root(output_root) / "projects")


def _project_dir(output_root: str, project_id: str) -> Path:
    return _projects_root(output_root) / _safe_id(project_id)


def _project_record_path(output_root: str, project_id: str) -> Path:
    return _project_dir(output_root, project_id) / "project.json"


def _rounds_dir(output_root: str, project_id: str) -> Path:
    return ensure_dir(_project_dir(output_root, project_id) / "rounds")


def _round_record_path(output_root: str, project_id: str, round_id: str) -> Path:
    return _rounds_dir(output_root, project_id) / f"{_safe_id(round_id)}.json"


def _allocate_unique_record_id(
    *,
    preferred: str,
    fallback_prefix: str,
    path_for: Callable[[str], Path],
) -> str:
    base = _safe_id(preferred)
    if not preferred.strip() or base in {"", "id"}:
        base = _safe_id(fallback_prefix)
    candidate = base
    index = 2
    while path_for(candidate).exists():
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _normalize_owner(value: object | None) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    normalized = _normalize_user(value) or {}
    username = str(normalized.get("username") or "").strip()
    run_prefix = (
        str(raw.get("run_prefix") or "").strip() if isinstance(raw, dict) else ""
    )
    if not run_prefix and username:
        run_prefix = _safe_id(username)
    role = str(normalized.get("role") or "").strip().lower() or "user"
    return {
        "owner_username": username,
        "owner_run_prefix": run_prefix,
        "owner_role": role,
    }


def _user_is_admin(value: object | None) -> bool:
    owner = _normalize_owner(value)
    return owner.get("owner_role") == "admin"


def _record_visible_to_user(record: dict[str, Any], user: object | None) -> bool:
    if user is None or _user_is_admin(user):
        return True
    owner = _normalize_owner(user)
    if not owner["owner_username"] and not owner["owner_run_prefix"]:
        return False
    record_username = str(record.get("owner_username") or "").strip()
    record_run_prefix = str(record.get("owner_run_prefix") or "").strip()
    if record_username and record_username == owner["owner_username"]:
        return True
    if record_run_prefix and record_run_prefix == owner["owner_run_prefix"]:
        return True
    return False


def _require_record_access(
    record: dict[str, Any], user: object | None, *, kind: str
) -> None:
    if not _record_visible_to_user(record, user):
        raise ValueError(f"{kind} not allowed for this user")


def _record_status(record: dict[str, Any] | None) -> str:
    return str((record or {}).get("status") or "").strip().lower()


def _record_is_archived(record: dict[str, Any] | None) -> bool:
    return _record_status(record) == "archived"


def _record_listed_for_user(
    record: dict[str, Any], user: object | None, *, include_archived: bool
) -> bool:
    if not _record_visible_to_user(record, user):
        return False
    if include_archived:
        return True
    return not _record_is_archived(record)


def _load_json_record(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = read_json(path)
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return None


def _sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("round_id") or item.get("project_id") or ""),
        ),
        reverse=True,
    )


def _save_project(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    owner = _normalize_owner(user)
    name = _as_text(arguments.get("name")).strip()
    if not name:
        raise ValueError("name is required")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if raw_project_id:
        project_id = _safe_id(raw_project_id)
    else:
        project_id = _allocate_unique_record_id(
            preferred=name,
            fallback_prefix="project",
            path_for=lambda candidate: _project_record_path(
                runner.output_root, candidate
            ),
        )
    path = _project_record_path(runner.output_root, project_id)
    existing = _load_json_record(path)
    if existing is not None:
        _require_record_access(existing, user, kind="project")
    created_at = str((existing or {}).get("created_at") or _now_iso())
    record: dict[str, Any] = {
        "project_id": project_id,
        "name": name,
        "status": _as_text(arguments.get("status")).strip()
        or str((existing or {}).get("status") or "active"),
        "description": _as_text(arguments.get("description")).strip()
        or str((existing or {}).get("description") or ""),
        "target_summary": _as_text(arguments.get("target_summary")).strip()
        or str((existing or {}).get("target_summary") or ""),
        "created_by": str(
            (existing or {}).get("created_by") or owner.get("owner_username") or ""
        ),
        "created_at": created_at,
        "updated_at": _now_iso(),
        "owner_username": str(
            (existing or {}).get("owner_username") or owner.get("owner_username") or ""
        ),
        "owner_run_prefix": str(
            (existing or {}).get("owner_run_prefix")
            or owner.get("owner_run_prefix")
            or ""
        ),
        "owner_role": str(
            (existing or {}).get("owner_role") or owner.get("owner_role") or ""
        ),
    }
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"saved": True, "path": rel_path, "project": record}


def _list_projects(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    limit = _as_int(arguments.get("limit"), 200)
    include_archived = _as_bool(arguments.get("include_archived"), False)
    items: list[dict[str, Any]] = []
    for path in sorted(_projects_root(runner.output_root).glob("*/project.json")):
        record = _load_json_record(path)
        if record is None or not _record_listed_for_user(
            record, user, include_archived=include_archived
        ):
            continue
        items.append(record)
    return {"projects": _sort_records(items)[: max(0, limit)]}


def _get_project(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    project_id = _safe_id(raw_project_id)
    record = _load_json_record(_project_record_path(runner.output_root, project_id))
    if record is None:
        return {"found": False, "project": None}
    _require_record_access(record, arguments.get("user"), kind="project")
    return {"found": True, "project": record}


def _require_request_metadata_access(
    runner: PipelineRunner,
    *,
    project_id: object | None,
    round_id: object | None,
    user: object | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    raw_project_id = _as_text(project_id).strip()
    raw_round_id = _as_text(round_id).strip()
    if raw_round_id and not raw_project_id:
        raise ValueError("round_id requires project_id")
    if not raw_project_id:
        return None, None
    project = _load_json_record(
        _project_record_path(runner.output_root, _safe_id(raw_project_id))
    )
    if project is None:
        raise ValueError("project_id not found")
    _require_record_access(project, user, kind="project")
    if not raw_round_id:
        return project, None
    round_record = _load_json_record(
        _round_record_path(
            runner.output_root, _safe_id(raw_project_id), _safe_id(raw_round_id)
        )
    )
    if round_record is None:
        raise ValueError("round_id not found")
    _require_record_access(round_record, user, kind="round")
    return project, round_record


def _save_round(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    owner = _normalize_owner(user)
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    project_id = _safe_id(raw_project_id)
    project = _load_json_record(_project_record_path(runner.output_root, project_id))
    if project is None:
        raise ValueError("project_id not found")
    _require_record_access(project, user, kind="project")
    title = _as_text(arguments.get("title")).strip()
    raw_round_id = _as_text(arguments.get("round_id")).strip()
    if raw_round_id:
        round_id = _safe_id(raw_round_id)
    else:
        round_id = _allocate_unique_record_id(
            preferred=title,
            fallback_prefix="round",
            path_for=lambda candidate: _round_record_path(
                runner.output_root, project_id, candidate
            ),
        )
    path = _round_record_path(runner.output_root, project_id, round_id)
    existing = _load_json_record(path)
    if existing is not None:
        _require_record_access(existing, user, kind="round")
    if not title:
        title = str((existing or {}).get("title") or round_id)
    record: dict[str, Any] = {
        "round_id": round_id,
        "project_id": project_id,
        "parent_round_id": _as_text(arguments.get("parent_round_id")).strip()
        or str((existing or {}).get("parent_round_id") or ""),
        "title": title,
        "goal": _as_text(arguments.get("goal")).strip()
        or str((existing or {}).get("goal") or ""),
        "hypothesis": _as_text(arguments.get("hypothesis")).strip()
        or str((existing or {}).get("hypothesis") or ""),
        "notes": _as_text(arguments.get("notes")).strip()
        or str((existing or {}).get("notes") or ""),
        "next_round_notes": _as_text(arguments.get("next_round_notes")).strip()
        or str((existing or {}).get("next_round_notes") or ""),
        "status": _as_text(arguments.get("status")).strip()
        or str((existing or {}).get("status") or "planned"),
        "linked_run_ids": _as_list_of_str(arguments.get("linked_run_ids"))
        or list((existing or {}).get("linked_run_ids") or []),
        "selected_candidates": _safe_json(arguments.get("selected_candidates"))
        if "selected_candidates" in arguments
        else _safe_json((existing or {}).get("selected_candidates") or []),
        "experiment_summary": _safe_json(arguments.get("experiment_summary"))
        if "experiment_summary" in arguments
        else _safe_json((existing or {}).get("experiment_summary") or {}),
        "created_by": str(
            (existing or {}).get("created_by")
            or owner.get("owner_username")
            or project.get("created_by")
            or ""
        ),
        "created_at": str((existing or {}).get("created_at") or _now_iso()),
        "updated_at": _now_iso(),
        "owner_username": str(
            (existing or {}).get("owner_username")
            or owner.get("owner_username")
            or project.get("owner_username")
            or ""
        ),
        "owner_run_prefix": str(
            (existing or {}).get("owner_run_prefix")
            or owner.get("owner_run_prefix")
            or project.get("owner_run_prefix")
            or ""
        ),
        "owner_role": str(
            (existing or {}).get("owner_role")
            or owner.get("owner_role")
            or project.get("owner_role")
            or ""
        ),
    }
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"saved": True, "path": rel_path, "round": record}


def _list_rounds(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    limit = _as_int(arguments.get("limit"), 500)
    include_archived = _as_bool(arguments.get("include_archived"), False)
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    project_id = _safe_id(raw_project_id) if raw_project_id else ""
    items: list[dict[str, Any]] = []
    if project_id:
        project = _load_json_record(
            _project_record_path(runner.output_root, project_id)
        )
        if project is None or not _record_listed_for_user(
            project, user, include_archived=include_archived
        ):
            return {"project_id": project_id, "rounds": []}
        paths = sorted(_rounds_dir(runner.output_root, project_id).glob("*.json"))
    else:
        paths = sorted(_projects_root(runner.output_root).glob("*/rounds/*.json"))
    for path in paths:
        record = _load_json_record(path)
        if record is None or not _record_listed_for_user(
            record, user, include_archived=include_archived
        ):
            continue
        items.append(record)
    return {
        "project_id": project_id or None,
        "rounds": _sort_records(items)[: max(0, limit)],
    }


def _get_round(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    raw_round_id = _as_text(arguments.get("round_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    if not raw_round_id:
        raise ValueError("round_id is required")
    project_id = _safe_id(raw_project_id)
    round_id = _safe_id(raw_round_id)
    record = _load_json_record(
        _round_record_path(runner.output_root, project_id, round_id)
    )
    if record is None:
        return {"found": False, "round": None}
    _require_record_access(record, arguments.get("user"), kind="round")
    return {"found": True, "round": record}


def _archive_project(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    project_id = _safe_id(raw_project_id)
    path = _project_record_path(runner.output_root, project_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "archived": False, "project": None}
    _require_record_access(record, user, kind="project")
    record["status"] = "archived"
    record["updated_at"] = _now_iso()
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"found": True, "archived": True, "path": rel_path, "project": record}


def _restore_project(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    project_id = _safe_id(raw_project_id)
    path = _project_record_path(runner.output_root, project_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "restored": False, "project": None}
    _require_record_access(record, user, kind="project")
    record["status"] = "active"
    record["updated_at"] = _now_iso()
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"found": True, "restored": True, "path": rel_path, "project": record}


def _archive_round(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    raw_round_id = _as_text(arguments.get("round_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    if not raw_round_id:
        raise ValueError("round_id is required")
    project_id = _safe_id(raw_project_id)
    round_id = _safe_id(raw_round_id)
    path = _round_record_path(runner.output_root, project_id, round_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "archived": False, "round": None}
    _require_record_access(record, user, kind="round")
    record["status"] = "archived"
    record["updated_at"] = _now_iso()
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"found": True, "archived": True, "path": rel_path, "round": record}


def _restore_round(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    raw_round_id = _as_text(arguments.get("round_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    if not raw_round_id:
        raise ValueError("round_id is required")
    project_id = _safe_id(raw_project_id)
    round_id = _safe_id(raw_round_id)
    path = _round_record_path(runner.output_root, project_id, round_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "restored": False, "round": None}
    _require_record_access(record, user, kind="round")
    record["status"] = "active"
    record["updated_at"] = _now_iso()
    write_json(path, record)
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    return {"found": True, "restored": True, "path": rel_path, "round": record}


def _delete_round_record(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    raw_round_id = _as_text(arguments.get("round_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    if not raw_round_id:
        raise ValueError("round_id is required")
    project_id = _safe_id(raw_project_id)
    round_id = _safe_id(raw_round_id)
    path = _round_record_path(runner.output_root, project_id, round_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "deleted": False, "round": None}
    _require_record_access(record, user, kind="round")
    rel_path = path.relative_to(_workspace_root(runner.output_root)).as_posix()
    path.unlink()
    rounds_dir = path.parent
    try:
        if rounds_dir.exists() and not any(rounds_dir.iterdir()):
            rounds_dir.rmdir()
    except OSError:
        pass
    return {"found": True, "deleted": True, "path": rel_path, "round": record}


def _delete_project_record(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    user = arguments.get("user")
    raw_project_id = _as_text(arguments.get("project_id")).strip()
    if not raw_project_id:
        raise ValueError("project_id is required")
    delete_rounds = _as_bool(arguments.get("delete_rounds"), False)
    project_id = _safe_id(raw_project_id)
    path = _project_record_path(runner.output_root, project_id)
    record = _load_json_record(path)
    if record is None:
        return {"found": False, "deleted": False, "project": None}
    _require_record_access(record, user, kind="project")
    project_dir = _project_dir(runner.output_root, project_id)
    round_paths = (
        sorted((project_dir / "rounds").glob("*.json"))
        if (project_dir / "rounds").exists()
        else []
    )
    if round_paths and not delete_rounds:
        raise ValueError(
            "project has rounds; pass delete_rounds=true to delete metadata"
        )
    rel_path = project_dir.relative_to(_workspace_root(runner.output_root)).as_posix()
    shutil.rmtree(project_dir)
    return {
        "found": True,
        "deleted": True,
        "path": rel_path,
        "deleted_round_count": len(round_paths),
        "project": record,
    }


def _load_report_text(output_root: str, run_id: str) -> str | None:
    root = resolve_run_path(output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")
    report_path = root / "report.md"
    if not report_path.exists():
        return None
    return report_path.read_text(encoding="utf-8")


def _save_report_text(output_root: str, run_id: str, content: str) -> None:
    root = resolve_run_path(output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")
    report_path = root / "report.md"
    report_path.write_text(content, encoding="utf-8")


def _save_report_text_ko(output_root: str, run_id: str, content: str) -> None:
    root = resolve_run_path(output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")
    report_path = root / "report_ko.md"
    report_path.write_text(content, encoding="utf-8")


def _save_report_attachments(
    output_root: str,
    run_id: str,
    attachments: object | None,
) -> list[dict[str, object]]:
    if attachments is None:
        return []
    if not isinstance(attachments, list):
        raise ValueError("attachments must be an array")

    root = resolve_run_path(output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")

    total_bytes = 0
    max_total_bytes = 8_000_000
    max_file_bytes = 2_000_000
    saved: list[dict[str, object]] = []

    for idx, item in enumerate(attachments):
        if not isinstance(item, dict):
            raise ValueError(f"attachments[{idx}] must be an object")

        raw_path = str(item.get("path") or "").strip().replace("\\", "/")
        if not raw_path:
            raise ValueError(f"attachments[{idx}].path is required")
        rel_path = raw_path.lstrip("/")
        if rel_path.startswith("./"):
            rel_path = rel_path[2:]
        if not rel_path:
            raise ValueError(f"attachments[{idx}].path is invalid")
        if not rel_path.startswith("report_assets/"):
            raise ValueError(
                f"attachments[{idx}].path must start with 'report_assets/'"
            )

        text_value = item.get("text")
        base64_value = item.get("base64")
        if text_value is None and base64_value is None:
            raise ValueError(f"attachments[{idx}] requires text or base64")

        data: bytes
        if base64_value is not None:
            try:
                data = base64.b64decode(str(base64_value), validate=True)
            except Exception as exc:
                raise ValueError(f"attachments[{idx}].base64 is invalid") from exc
        else:
            data = _as_text(text_value).encode("utf-8")

        if len(data) > max_file_bytes:
            raise ValueError(
                f"attachments[{idx}] is too large (max {max_file_bytes} bytes)"
            )
        total_bytes += len(data)
        if total_bytes > max_total_bytes:
            raise ValueError(f"attachments total size exceeds {max_total_bytes} bytes")

        path = resolve_run_path(output_root, run_id, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        saved_item: dict[str, object] = {
            "path": rel_path,
            "size_bytes": len(data),
        }
        content_type = str(item.get("content_type") or "").strip()
        if content_type:
            saved_item["content_type"] = content_type
        saved.append(saved_item)

    return saved


def _summarize_feedback(items: list[dict[str, object]]) -> dict[str, object]:
    counts = {"good": 0, "bad": 0}
    for item in items:
        rating = str(item.get("rating") or "").lower()
        if rating in counts:
            counts[rating] += 1
    return counts


def _summarize_experiments(items: list[dict[str, object]]) -> dict[str, object]:
    counts = {"success": 0, "fail": 0, "inconclusive": 0}
    for item in items:
        result = str(item.get("result") or "").lower()
        if result in counts:
            counts[result] += 1
    return counts


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    nums = sorted(values)
    mid = len(nums) // 2
    if len(nums) % 2 == 1:
        return float(nums[mid])
    return float(nums[mid - 1] + nums[mid]) / 2.0


def _percentile_from_sorted(nums: list[float], q: float) -> float | None:
    if not nums:
        return None
    if len(nums) == 1:
        return float(nums[0])
    qq = min(1.0, max(0.0, float(q)))
    idx = (len(nums) - 1) * qq
    lo = int(idx)
    hi = min(lo + 1, len(nums) - 1)
    if lo == hi:
        return float(nums[lo])
    frac = idx - lo
    return float(nums[lo] * (1.0 - frac) + nums[hi] * frac)


def _distribution_stats(values: list[float]) -> dict[str, float | int | None]:
    nums = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not nums:
        return {
            "count": 0,
            "median": None,
            "p10": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "iqr": None,
        }
    p25 = _percentile_from_sorted(nums, 0.25)
    p75 = _percentile_from_sorted(nums, 0.75)
    iqr = float(p75 - p25) if p25 is not None and p75 is not None else None
    return {
        "count": len(nums),
        "median": _median(nums),
        "p10": _percentile_from_sorted(nums, 0.10),
        "p25": p25,
        "p75": p75,
        "p90": _percentile_from_sorted(nums, 0.90),
        "iqr": iqr,
    }


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _normalize_sequence(raw: object) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    return re.sub(r"[^A-Z]", "", text)


def _sequence_identity(seq_a: str, seq_b: str) -> float | None:
    stats = _sequence_difference_stats(seq_a, seq_b)
    if not isinstance(stats, dict):
        return None
    ident = stats.get("identity")
    return float(ident) if isinstance(ident, (int, float)) else None


def _sequence_difference_stats(
    wt_seq: str, design_seq: str
) -> dict[str, object] | None:
    wt = _normalize_sequence(wt_seq)
    design = _normalize_sequence(design_seq)
    if not wt or not design:
        return None
    compare_len = max(len(wt), len(design))
    if compare_len <= 0:
        return None
    span = min(len(wt), len(design))
    matches = 0
    for i in range(span):
        if wt[i] == design[i]:
            matches += 1
    diff_count = max(0, compare_len - matches)
    identity = float(matches) / float(compare_len)
    diff_ratio = float(diff_count) / float(compare_len)
    return {
        "wt_length": len(wt),
        "design_length": len(design),
        "compare_length": compare_len,
        "match_count": matches,
        "difference_count": diff_count,
        "difference_ratio": diff_ratio,
        "difference_pct": diff_ratio * 100.0,
        "identity": identity,
        "identity_pct": identity * 100.0,
    }


def _extract_design_chains_from_payload(payload: dict[str, object] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    for key in (
        "design_chains_used",
        "auto_selected_design_chains",
        "design_chains",
        "requested_design_chains",
    ):
        chains = _as_list_of_str(payload.get(key))
        if chains:
            return chains
    return []


def _load_primary_design_chains(run_root: Path | None) -> list[str]:
    if run_root is None:
        return []
    for rel_path in ("query_pdb_alignment.json", "chain_strategy.json"):
        payload = _load_json_file(run_root / rel_path)
        if not isinstance(payload, dict):
            continue
        chains = _extract_design_chains_from_payload(payload)
        if chains:
            return chains
    return []


def _extract_primary_target_sequence(
    request: dict[str, object] | None,
    *,
    run_root: Path | None = None,
) -> str | None:
    if not isinstance(request, dict):
        return None
    fasta_text = _as_text(request.get("target_fasta")).strip()
    if fasta_text:
        try:
            records = _parse_fasta_or_sequence(fasta_text)
        except Exception:
            records = []
        for rec in records:
            seq = _normalize_sequence(rec.sequence)
            if seq:
                return seq
    target_pdb = _as_text(request.get("target_pdb")).strip()
    if not target_pdb:
        return None
    design_chains = _load_primary_design_chains(run_root)
    if not design_chains:
        design_chains = _as_list_of_str(request.get("design_chains"))
    try:
        rec = _target_record_from_pdb(target_pdb, design_chains=design_chains or None)
    except Exception:
        return None
    seq = _normalize_sequence(rec.sequence)
    return seq or None


def _collect_design_sequences(
    summary: dict[str, object] | None,
    *,
    hide_target: bool = False,
) -> list[str]:
    if not isinstance(summary, dict):
        return []
    tiers = summary.get("tiers")
    if not isinstance(tiers, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        samples = tier.get("proteinmpnn_samples")
        if not isinstance(samples, list):
            continue
        visible_seq_sources = _visible_sample_sources(samples, hide_target=hide_target)
        use_visible_filter = bool(samples)
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            seq_id = str(sample.get("id") or "").strip()
            if not _should_include_seq_id(
                seq_id, visible_seq_sources, use_visible_filter=use_visible_filter
            ):
                continue
            seq = _normalize_sequence(sample.get("sequence"))
            if not seq or seq in seen:
                continue
            seen.add(seq)
            out.append(seq)
    return out


def _build_diversity_summary(
    *,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
) -> dict[str, object]:
    wt_seq = _extract_primary_target_sequence(request)
    hide_target = _should_hide_target_source(summary)
    design_seqs = _collect_design_sequences(summary, hide_target=hide_target)

    wt_id_values: list[float] = []
    if wt_seq:
        for seq in design_seqs:
            ident = _sequence_identity(wt_seq, seq)
            if ident is not None:
                wt_id_values.append(float(ident))

    max_pairwise_sequences = 300
    pairwise_input = design_seqs[:max_pairwise_sequences]
    pairwise_values: list[float] = []
    for i in range(len(pairwise_input)):
        left = pairwise_input[i]
        for j in range(i + 1, len(pairwise_input)):
            right = pairwise_input[j]
            ident = _sequence_identity(left, right)
            if ident is not None:
                pairwise_values.append(float(ident))

    wt_stats = _distribution_stats(wt_id_values)
    pairwise_stats = _distribution_stats(pairwise_values)
    wt_stats["best"] = max(wt_id_values) if wt_id_values else None
    wt_stats["worst"] = min(wt_id_values) if wt_id_values else None
    return {
        "design_unique_sequences": len(design_seqs),
        "wt_identity": wt_stats,
        "design_pairwise_identity": {
            **pairwise_stats,
            "sequence_count": len(pairwise_input),
            "truncated": len(design_seqs) > max_pairwise_sequences,
            "evaluated_pairs": len(pairwise_values),
        },
    }


def _load_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _af2_payload_has_recovered_failure(af2: dict[str, object] | None) -> bool:
    if not isinstance(af2, dict):
        return False
    return bool(af2.get("recovered"))


def _relax_payload_has_recovered_failure(relax: dict[str, object] | None) -> bool:
    if not isinstance(relax, dict):
        return False
    return bool(relax.get("recovered"))


def _collect_design_metrics(
    run_root: Path,
    summary: dict[str, object] | None,
    *,
    hide_target: bool = False,
) -> dict[str, object]:
    out = {
        "soluprot_scores": [],
        "soluprot_total": 0,
        "soluprot_passed": 0,
        "af2_candidate_total": 0,
        "af2_plddt": [],
        "af2_rmsd": [],
        "af2_target_rmsd": [],
        "af2_selected_plddt": [],
        "af2_selected_rmsd": [],
        "af2_selected_target_rmsd": [],
        "af2_selected_total": 0,
        "relax_candidate_total": 0,
        "relax_score_per_residue": [],
        "relax_selected_score_per_residue": [],
        "relax_selected_total": 0,
    }
    if not summary:
        return out
    tiers = summary.get("tiers")
    if not isinstance(tiers, list):
        return out
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        tier_val = tier.get("tier")
        if tier_val is None:
            continue
        try:
            tier_key = _tier_key(float(tier_val))
        except Exception:
            continue
        tier_dir = run_root / "tiers" / tier_key
        samples = (
            tier.get("proteinmpnn_samples")
            if isinstance(tier.get("proteinmpnn_samples"), list)
            else []
        )
        visible_seq_sources = _visible_sample_sources(samples, hide_target=hide_target)
        use_visible_filter = bool(samples)

        sol = _load_json_file(tier_dir / "soluprot.json")
        if isinstance(sol, dict):
            scores = sol.get("scores")
            passed_ids = (
                sol.get("passed_ids") if isinstance(sol.get("passed_ids"), list) else []
            )
            if isinstance(scores, dict):
                values = [
                    float(v)
                    for seq_id, v in scores.items()
                    if isinstance(v, (int, float))
                    and _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
                out["soluprot_scores"].extend(values)
                out["soluprot_total"] += len(values)
            out["soluprot_passed"] += len(
                _filtered_metric_ids(
                    passed_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
            )

        af2 = _load_json_file(tier_dir / "af2_scores.json")
        if isinstance(af2, dict):
            recovered_failure = _af2_payload_has_recovered_failure(af2)
            scores = (
                af2.get("scores")
                if isinstance(af2.get("scores"), dict) and not recovered_failure
                else {}
            )
            rmsd_scores = (
                af2.get("rmsd_scores")
                if isinstance(af2.get("rmsd_scores"), dict) and not recovered_failure
                else {}
            )
            target_rmsd_scores = (
                af2.get("target_rmsd_scores")
                if isinstance(af2.get("target_rmsd_scores"), dict)
                and not recovered_failure
                else {}
            )
            candidate_ids = (
                af2.get("candidate_ids")
                if isinstance(af2.get("candidate_ids"), list)
                else []
            )
            filtered_candidate_ids = _filtered_metric_ids(
                candidate_ids,
                visible_seq_sources,
                use_visible_filter=use_visible_filter,
            )
            candidate_total = len(filtered_candidate_ids)
            if candidate_total <= 0 and isinstance(
                af2.get("candidate_count_after_budget"), int
            ):
                candidate_total = (
                    int(af2.get("candidate_count_after_budget") or 0)
                    if not use_visible_filter
                    else 0
                )
            if candidate_total <= 0:
                candidate_total = len(
                    [
                        seq_id
                        for seq_id in scores.keys()
                        if _should_include_seq_id(
                            seq_id,
                            visible_seq_sources,
                            use_visible_filter=use_visible_filter,
                        )
                    ]
                )
            out["af2_candidate_total"] += max(0, candidate_total)
            candidate_metric_ids = (
                filtered_candidate_ids
                if candidate_ids
                else [
                    str(seq_id)
                    for seq_id in scores.keys()
                    if _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
            )
            for seq_id in candidate_metric_ids:
                if seq_id in scores and isinstance(scores.get(seq_id), (int, float)):
                    out["af2_plddt"].append(float(scores.get(seq_id)))
                if seq_id in rmsd_scores and isinstance(
                    rmsd_scores.get(seq_id), (int, float)
                ):
                    out["af2_rmsd"].append(float(rmsd_scores.get(seq_id)))
                if seq_id in target_rmsd_scores and isinstance(
                    target_rmsd_scores.get(seq_id), (int, float)
                ):
                    out["af2_target_rmsd"].append(float(target_rmsd_scores.get(seq_id)))
            selected_ids = (
                af2.get("selected_ids")
                if isinstance(af2.get("selected_ids"), list) and not recovered_failure
                else []
            )
            if selected_ids:
                filtered_selected_ids = _filtered_metric_ids(
                    selected_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
                out["af2_selected_total"] += len(filtered_selected_ids)
                for seq_id in filtered_selected_ids:
                    if seq_id in scores and isinstance(
                        scores.get(seq_id), (int, float)
                    ):
                        out["af2_selected_plddt"].append(float(scores.get(seq_id)))
                    if seq_id in rmsd_scores and isinstance(
                        rmsd_scores.get(seq_id), (int, float)
                    ):
                        out["af2_selected_rmsd"].append(float(rmsd_scores.get(seq_id)))
                    if seq_id in target_rmsd_scores and isinstance(
                        target_rmsd_scores.get(seq_id), (int, float)
                    ):
                        out["af2_selected_target_rmsd"].append(
                            float(target_rmsd_scores.get(seq_id))
                        )

        relax = _load_json_file(tier_dir / "relax_scores.json")
        if isinstance(relax, dict):
            recovered_failure = _relax_payload_has_recovered_failure(relax)
            score_per_residue = (
                relax.get("score_per_residue")
                if isinstance(relax.get("score_per_residue"), dict)
                and not recovered_failure
                else {}
            )
            candidate_ids = (
                relax.get("candidate_ids")
                if isinstance(relax.get("candidate_ids"), list)
                else []
            )
            filtered_candidate_ids = _filtered_metric_ids(
                candidate_ids,
                visible_seq_sources,
                use_visible_filter=use_visible_filter,
            )
            candidate_total = len(filtered_candidate_ids)
            if candidate_total <= 0:
                candidate_total = len(
                    [
                        seq_id
                        for seq_id in score_per_residue.keys()
                        if _should_include_seq_id(
                            seq_id,
                            visible_seq_sources,
                            use_visible_filter=use_visible_filter,
                        )
                    ]
                )
            out["relax_candidate_total"] += max(0, candidate_total)
            candidate_metric_ids = (
                filtered_candidate_ids
                if candidate_ids
                else [
                    str(seq_id)
                    for seq_id in score_per_residue.keys()
                    if _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
            )
            for seq_id in candidate_metric_ids:
                if seq_id in score_per_residue and isinstance(
                    score_per_residue.get(seq_id), (int, float)
                ):
                    out["relax_score_per_residue"].append(
                        float(score_per_residue.get(seq_id))
                    )
            selected_ids = (
                relax.get("selected_ids")
                if isinstance(relax.get("selected_ids"), list) and not recovered_failure
                else []
            )
            if selected_ids:
                filtered_selected_ids = _filtered_metric_ids(
                    selected_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
                out["relax_selected_total"] += len(filtered_selected_ids)
                for seq_id in filtered_selected_ids:
                    if seq_id in score_per_residue and isinstance(
                        score_per_residue.get(seq_id), (int, float)
                    ):
                        out["relax_selected_score_per_residue"].append(
                            float(score_per_residue.get(seq_id))
                        )
    return out


def _classify_backbone_source(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value.startswith("rfd3"):
        return "rfd3"
    if value.startswith("bioemu"):
        return "bioemu"
    if value == "target":
        return "target"
    return "other"


def _normalize_backbone_source(raw: object) -> str:
    source = _classify_backbone_source(raw)
    if source == "target":
        return "other"
    return source


def _visible_backbone_source(raw: object, *, hide_target: bool) -> str | None:
    source = _classify_backbone_source(raw)
    if source == "target":
        return None if hide_target else "other"
    return source


def _should_hide_target_source(
    summary: dict[str, object] | None,
    *,
    run_root: Path | None = None,
) -> bool:
    sources: set[str] = set()
    if isinstance(summary, dict):
        tiers = summary.get("tiers")
        if isinstance(tiers, list):
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                samples = tier.get("proteinmpnn_samples")
                if not isinstance(samples, list):
                    continue
                for sample in samples:
                    if not isinstance(sample, dict):
                        continue
                    meta = (
                        sample.get("meta")
                        if isinstance(sample.get("meta"), dict)
                        else {}
                    )
                    sources.add(_classify_backbone_source(meta.get("backbone_source")))
    if run_root is not None:
        backbones = _load_json_file(run_root / "backbones.json")
        if isinstance(backbones, dict):
            manifest_sources = backbones.get("sources")
            if isinstance(manifest_sources, dict):
                for raw_source in manifest_sources:
                    sources.add(_classify_backbone_source(raw_source))
            items = backbones.get("backbones")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sources.add(_classify_backbone_source(item.get("source")))
    return "target" in sources and bool({"rfd3", "bioemu"} & sources)


def _visible_sample_sources(
    samples: list[object] | None,
    *,
    hide_target: bool,
) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(samples, list):
        return out
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        seq_id = str(sample.get("id") or "").strip()
        if not seq_id:
            continue
        meta = sample.get("meta") if isinstance(sample.get("meta"), dict) else {}
        source = _visible_backbone_source(
            meta.get("backbone_source"), hide_target=hide_target
        )
        if source is None:
            continue
        out[seq_id] = source
    return out


def _should_include_seq_id(
    seq_id: object, visible_seq_sources: dict[str, str], *, use_visible_filter: bool
) -> bool:
    if not use_visible_filter:
        return True
    return str(seq_id or "").strip() in visible_seq_sources


def _filtered_metric_ids(
    seq_ids: list[object],
    visible_seq_sources: dict[str, str],
    *,
    use_visible_filter: bool,
) -> list[str]:
    out: list[str] = []
    for seq_id in seq_ids:
        seq = str(seq_id or "").strip()
        if not seq:
            continue
        if not _should_include_seq_id(
            seq, visible_seq_sources, use_visible_filter=use_visible_filter
        ):
            continue
        out.append(seq)
    return out


def _source_for_sequence_id(
    seq_id: str, lookup: dict[str, str], *, hide_target: bool = False
) -> str | None:
    seq = str(seq_id or "").strip()
    if not seq:
        return None
    backbone_id = seq.split(":", 1)[0]
    raw_source = _classify_backbone_source(backbone_id)
    if raw_source == "target":
        return None if hide_target else "other"
    mapped = lookup.get(backbone_id)
    if mapped:
        return mapped
    source = _visible_backbone_source(backbone_id, hide_target=hide_target)
    if source is not None and source != "other":
        return source
    low = backbone_id.lower()
    if low.startswith("rfd3"):
        return "rfd3"
    if low.startswith("bioemu"):
        return "bioemu"
    return "other"


def _source_metrics_bucket() -> dict[str, object]:
    return {
        "backbone_count": 0,
        "requested_count": 0,
        "observed_count": 0,
        "materialized_count": 0,
        "propagated_count": 0,
        "propagation_mode": "",
        "selected_backbone_id": None,
        "soluprot_scores": [],
        "soluprot_total": 0,
        "soluprot_passed": 0,
        "af2_candidate_total": 0,
        "af2_candidate_plddt": [],
        "af2_candidate_rmsd": [],
        "af2_selected_plddt": [],
        "af2_selected_rmsd": [],
        "af2_selected_total": 0,
        "relax_candidate_total": 0,
        "relax_candidate_score_per_residue": [],
        "relax_selected_score_per_residue": [],
        "relax_selected_total": 0,
    }


def _source_propagation_mode(
    observed_count: object,
    materialized_count: object,
    propagated_count: object,
) -> str:
    observed = int(observed_count or 0)
    materialized = int(materialized_count or 0)
    propagated = int(propagated_count or 0)
    if propagated <= 0:
        return "none"
    available = observed if observed > materialized else materialized
    if available <= 0:
        return "propagated_only"
    if propagated >= available:
        return "all_observed" if observed > materialized else "all_materialized"
    if propagated == 1 and available > 1:
        return "selected_only"
    return "partial"


def _collect_source_metrics(
    run_root: Path,
    summary: dict[str, object] | None,
    *,
    hide_target: bool = False,
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {
        "rfd3": _source_metrics_bucket(),
        "bioemu": _source_metrics_bucket(),
        "other": _source_metrics_bucket(),
    }

    backbone_source_by_id: dict[str, str] = {}
    manifest_sources_present: set[str] = set()
    backbones = _load_json_file(run_root / "backbones.json")
    if isinstance(backbones, dict):
        manifest_sources = backbones.get("sources")
        if isinstance(manifest_sources, dict):
            for raw_source, raw_summary in manifest_sources.items():
                if not isinstance(raw_summary, dict):
                    continue
                source = _visible_backbone_source(raw_source, hide_target=hide_target)
                if source is None:
                    continue
                manifest_sources_present.add(source)
                bucket = out[source]
                requested_count = int(raw_summary.get("requested_count") or 0)
                observed_count = int(raw_summary.get("observed_count") or 0)
                materialized_count = int(raw_summary.get("materialized_count") or 0)
                propagated_count = int(raw_summary.get("propagated_count") or 0)
                bucket["requested_count"] = requested_count
                bucket["observed_count"] = observed_count
                bucket["materialized_count"] = materialized_count
                bucket["propagated_count"] = propagated_count
                bucket["backbone_count"] = propagated_count
                bucket["propagation_mode"] = str(
                    raw_summary.get("propagation_mode") or ""
                ).strip()
                selected_backbone_id = str(
                    raw_summary.get("selected_backbone_id") or ""
                ).strip()
                if selected_backbone_id:
                    bucket["selected_backbone_id"] = selected_backbone_id
        items = backbones.get("backbones")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = _visible_backbone_source(
                    item.get("source"), hide_target=hide_target
                )
                if source is None:
                    continue
                backbone_id = str(item.get("id") or "").strip()
                if backbone_id:
                    backbone_source_by_id[backbone_id] = source
                if (
                    bool(item.get("selected"))
                    and backbone_id
                    and not str(out[source].get("selected_backbone_id") or "").strip()
                ):
                    out[source]["selected_backbone_id"] = backbone_id
                if source in manifest_sources_present:
                    continue
                if bool(item.get("materialized", True)):
                    out[source]["materialized_count"] = (
                        int(out[source].get("materialized_count") or 0) + 1
                    )
                if bool(item.get("propagated", True)):
                    out[source]["propagated_count"] = (
                        int(out[source].get("propagated_count") or 0) + 1
                    )
                    out[source]["backbone_count"] = (
                        int(out[source].get("backbone_count") or 0) + 1
                    )
        for source, bucket in out.items():
            if source not in manifest_sources_present:
                bucket["observed_count"] = max(
                    int(bucket.get("observed_count") or 0),
                    int(bucket.get("materialized_count") or 0),
                )
                bucket["requested_count"] = max(
                    int(bucket.get("requested_count") or 0),
                    int(bucket.get("observed_count") or 0),
                )
                bucket["propagation_mode"] = _source_propagation_mode(
                    bucket.get("observed_count"),
                    bucket.get("materialized_count"),
                    bucket.get("propagated_count"),
                )

    if not summary:
        return out
    tiers = summary.get("tiers")
    if not isinstance(tiers, list):
        return out

    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        tier_val = tier.get("tier")
        if tier_val is None:
            continue
        try:
            tier_key = _tier_key(float(tier_val))
        except Exception:
            continue
        tier_dir = run_root / "tiers" / tier_key

        lookup = dict(backbone_source_by_id)
        bb_meta = _load_json_file(tier_dir / "proteinmpnn_backbones.json")
        if isinstance(bb_meta, dict):
            entries = bb_meta.get("backbones")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    backbone_id = str(entry.get("id") or "").strip()
                    if not backbone_id:
                        continue
                    source = _visible_backbone_source(
                        entry.get("source"), hide_target=hide_target
                    )
                    if source is None:
                        continue
                    lookup.setdefault(backbone_id, source)

        samples = (
            tier.get("proteinmpnn_samples")
            if isinstance(tier.get("proteinmpnn_samples"), list)
            else []
        )
        visible_seq_sources = _visible_sample_sources(samples, hide_target=hide_target)

        sol = _load_json_file(tier_dir / "soluprot.json")
        if isinstance(sol, dict):
            scores = sol.get("scores")
            passed_ids = (
                sol.get("passed_ids") if isinstance(sol.get("passed_ids"), list) else []
            )
            if isinstance(scores, dict):
                for seq_id, raw_score in scores.items():
                    if not isinstance(raw_score, (int, float)):
                        continue
                    source = visible_seq_sources.get(str(seq_id))
                    if source is None:
                        source = _source_for_sequence_id(
                            str(seq_id), lookup, hide_target=hide_target
                        )
                    if source is None:
                        continue
                    bucket = out[source]
                    bucket["soluprot_total"] = (
                        int(bucket.get("soluprot_total") or 0) + 1
                    )
                    cast_scores = bucket.get("soluprot_scores")
                    if isinstance(cast_scores, list):
                        cast_scores.append(float(raw_score))
            for seq_id in passed_ids:
                source = visible_seq_sources.get(str(seq_id))
                if source is None:
                    source = _source_for_sequence_id(
                        str(seq_id), lookup, hide_target=hide_target
                    )
                if source is None:
                    continue
                bucket = out[source]
                bucket["soluprot_passed"] = int(bucket.get("soluprot_passed") or 0) + 1

        af2 = _load_json_file(tier_dir / "af2_scores.json")
        if isinstance(af2, dict):
            recovered_failure = _af2_payload_has_recovered_failure(af2)
            scores = (
                af2.get("scores")
                if isinstance(af2.get("scores"), dict) and not recovered_failure
                else {}
            )
            rmsd_scores = (
                af2.get("rmsd_scores")
                if isinstance(af2.get("rmsd_scores"), dict) and not recovered_failure
                else {}
            )
            candidate_ids = (
                af2.get("candidate_ids")
                if isinstance(af2.get("candidate_ids"), list)
                else []
            )
            candidate_metric_ids = (
                candidate_ids if candidate_ids else list(scores.keys())
            )
            for seq_id in candidate_metric_ids:
                source = visible_seq_sources.get(str(seq_id))
                if source is None:
                    source = _source_for_sequence_id(
                        str(seq_id), lookup, hide_target=hide_target
                    )
                if source is None:
                    continue
                bucket = out[source]
                bucket["af2_candidate_total"] = (
                    int(bucket.get("af2_candidate_total") or 0) + 1
                )
                raw_plddt = scores.get(seq_id)
                if isinstance(raw_plddt, (int, float)):
                    cast_plddt = bucket.get("af2_candidate_plddt")
                    if isinstance(cast_plddt, list):
                        cast_plddt.append(float(raw_plddt))
                raw_rmsd = rmsd_scores.get(seq_id)
                if isinstance(raw_rmsd, (int, float)):
                    cast_rmsd = bucket.get("af2_candidate_rmsd")
                    if isinstance(cast_rmsd, list):
                        cast_rmsd.append(float(raw_rmsd))
            selected_ids = (
                af2.get("selected_ids")
                if isinstance(af2.get("selected_ids"), list) and not recovered_failure
                else []
            )
            for seq_id in selected_ids:
                source = visible_seq_sources.get(str(seq_id))
                if source is None:
                    source = _source_for_sequence_id(
                        str(seq_id), lookup, hide_target=hide_target
                    )
                if source is None:
                    continue
                bucket = out[source]
                bucket["af2_selected_total"] = (
                    int(bucket.get("af2_selected_total") or 0) + 1
                )
                raw_plddt = scores.get(seq_id)
                if isinstance(raw_plddt, (int, float)):
                    cast_plddt = bucket.get("af2_selected_plddt")
                    if isinstance(cast_plddt, list):
                        cast_plddt.append(float(raw_plddt))
                raw_rmsd = rmsd_scores.get(seq_id)
                if isinstance(raw_rmsd, (int, float)):
                    cast_rmsd = bucket.get("af2_selected_rmsd")
                    if isinstance(cast_rmsd, list):
                        cast_rmsd.append(float(raw_rmsd))

        relax = _load_json_file(tier_dir / "relax_scores.json")
        if isinstance(relax, dict):
            recovered_failure = _relax_payload_has_recovered_failure(relax)
            score_per_residue = (
                relax.get("score_per_residue")
                if isinstance(relax.get("score_per_residue"), dict)
                and not recovered_failure
                else {}
            )
            candidate_ids = (
                relax.get("candidate_ids")
                if isinstance(relax.get("candidate_ids"), list)
                else []
            )
            candidate_metric_ids = (
                candidate_ids if candidate_ids else list(score_per_residue.keys())
            )
            for seq_id in candidate_metric_ids:
                source = visible_seq_sources.get(str(seq_id))
                if source is None:
                    source = _source_for_sequence_id(
                        str(seq_id), lookup, hide_target=hide_target
                    )
                if source is None:
                    continue
                bucket = out[source]
                bucket["relax_candidate_total"] = (
                    int(bucket.get("relax_candidate_total") or 0) + 1
                )
                raw_relax = score_per_residue.get(seq_id)
                if isinstance(raw_relax, (int, float)):
                    cast_relax = bucket.get("relax_candidate_score_per_residue")
                    if isinstance(cast_relax, list):
                        cast_relax.append(float(raw_relax))
            selected_ids = (
                relax.get("selected_ids")
                if isinstance(relax.get("selected_ids"), list) and not recovered_failure
                else []
            )
            for seq_id in selected_ids:
                source = visible_seq_sources.get(str(seq_id))
                if source is None:
                    source = _source_for_sequence_id(
                        str(seq_id), lookup, hide_target=hide_target
                    )
                if source is None:
                    continue
                bucket = out[source]
                bucket["relax_selected_total"] = (
                    int(bucket.get("relax_selected_total") or 0) + 1
                )
                raw_relax = score_per_residue.get(seq_id)
                if isinstance(raw_relax, (int, float)):
                    cast_relax = bucket.get("relax_selected_score_per_residue")
                    if isinstance(cast_relax, list):
                        cast_relax.append(float(raw_relax))

    return out


def _collect_tier_compare_metrics(
    run_root: Path,
    summary: dict[str, object] | None,
    *,
    hide_target: bool = False,
) -> list[dict[str, object]]:
    if not isinstance(summary, dict):
        return []
    tiers = summary.get("tiers")
    if not isinstance(tiers, list):
        return []
    rows: list[dict[str, object]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        tier_val = tier.get("tier")
        if tier_val is None:
            continue
        try:
            tier_num = float(tier_val)
            tier_key = _tier_key(tier_num)
        except Exception:
            continue
        tier_dir = run_root / "tiers" / tier_key

        samples = (
            tier.get("proteinmpnn_samples")
            if isinstance(tier.get("proteinmpnn_samples"), list)
            else []
        )
        visible_seq_sources = _visible_sample_sources(samples, hide_target=hide_target)
        use_visible_filter = bool(samples)
        designs_total = len(visible_seq_sources) if use_visible_filter else len(samples)
        source_counts = {"rfd3": 0, "bioemu": 0, "other": 0}
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            seq_id = str(sample.get("id") or "").strip()
            if not _should_include_seq_id(
                seq_id, visible_seq_sources, use_visible_filter=use_visible_filter
            ):
                continue
            meta = sample.get("meta") if isinstance(sample.get("meta"), dict) else {}
            source = (
                _visible_backbone_source(
                    meta.get("backbone_source"), hide_target=hide_target
                )
                or "other"
            )
            if source not in source_counts:
                source = "other"
            source_counts[source] = int(source_counts.get(source) or 0) + 1

        sol = _load_json_file(tier_dir / "soluprot.json")
        sol_total = 0
        sol_passed = 0
        if isinstance(sol, dict):
            scores = sol.get("scores") if isinstance(sol.get("scores"), dict) else {}
            sol_total = len(
                [
                    1
                    for seq_id, v in scores.items()
                    if isinstance(v, (int, float))
                    and _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
            )
            passed_ids = (
                sol.get("passed_ids") if isinstance(sol.get("passed_ids"), list) else []
            )
            sol_passed = len(
                _filtered_metric_ids(
                    passed_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
            )

        af2 = _load_json_file(tier_dir / "af2_scores.json")
        af2_candidate_total = 0
        af2_selected_total = 0
        candidate_plddt: list[float] = []
        candidate_rmsd: list[float] = []
        if isinstance(af2, dict):
            recovered_failure = _af2_payload_has_recovered_failure(af2)
            scores = (
                af2.get("scores")
                if isinstance(af2.get("scores"), dict) and not recovered_failure
                else {}
            )
            rmsd_scores = (
                af2.get("rmsd_scores")
                if isinstance(af2.get("rmsd_scores"), dict) and not recovered_failure
                else {}
            )
            candidate_ids = (
                af2.get("candidate_ids")
                if isinstance(af2.get("candidate_ids"), list)
                else []
            )
            filtered_candidate_ids = _filtered_metric_ids(
                candidate_ids,
                visible_seq_sources,
                use_visible_filter=use_visible_filter,
            )
            af2_candidate_total = len(filtered_candidate_ids)
            if af2_candidate_total <= 0 and isinstance(
                af2.get("candidate_count_after_budget"), int
            ):
                af2_candidate_total = (
                    int(af2.get("candidate_count_after_budget") or 0)
                    if not use_visible_filter
                    else 0
                )
            if af2_candidate_total <= 0:
                af2_candidate_total = len(
                    [
                        seq_id
                        for seq_id in scores.keys()
                        if _should_include_seq_id(
                            seq_id,
                            visible_seq_sources,
                            use_visible_filter=use_visible_filter,
                        )
                    ]
                )
            candidate_metric_ids = (
                filtered_candidate_ids
                if candidate_ids
                else [
                    str(seq_id)
                    for seq_id in scores.keys()
                    if _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
            )
            for seq_id in candidate_metric_ids:
                raw_plddt = scores.get(seq_id)
                raw_rmsd = rmsd_scores.get(seq_id)
                if isinstance(raw_plddt, (int, float)):
                    candidate_plddt.append(float(raw_plddt))
                if isinstance(raw_rmsd, (int, float)):
                    candidate_rmsd.append(float(raw_rmsd))
            selected_ids = (
                af2.get("selected_ids")
                if isinstance(af2.get("selected_ids"), list) and not recovered_failure
                else []
            )
            af2_selected_total = len(
                _filtered_metric_ids(
                    selected_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
            )

        relax = _load_json_file(tier_dir / "relax_scores.json")
        relax_candidate_total = 0
        relax_selected_total = 0
        candidate_relax: list[float] = []
        if isinstance(relax, dict):
            recovered_failure = _relax_payload_has_recovered_failure(relax)
            score_per_residue = (
                relax.get("score_per_residue")
                if isinstance(relax.get("score_per_residue"), dict)
                and not recovered_failure
                else {}
            )
            candidate_ids = (
                relax.get("candidate_ids")
                if isinstance(relax.get("candidate_ids"), list)
                else []
            )
            filtered_candidate_ids = _filtered_metric_ids(
                candidate_ids,
                visible_seq_sources,
                use_visible_filter=use_visible_filter,
            )
            relax_candidate_total = len(filtered_candidate_ids)
            if relax_candidate_total <= 0:
                relax_candidate_total = len(
                    [
                        seq_id
                        for seq_id in score_per_residue.keys()
                        if _should_include_seq_id(
                            seq_id,
                            visible_seq_sources,
                            use_visible_filter=use_visible_filter,
                        )
                    ]
                )
            candidate_metric_ids = (
                filtered_candidate_ids
                if candidate_ids
                else [
                    str(seq_id)
                    for seq_id in score_per_residue.keys()
                    if _should_include_seq_id(
                        seq_id,
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                ]
            )
            for seq_id in candidate_metric_ids:
                raw_relax = score_per_residue.get(seq_id)
                if isinstance(raw_relax, (int, float)):
                    candidate_relax.append(float(raw_relax))
            selected_ids = (
                relax.get("selected_ids")
                if isinstance(relax.get("selected_ids"), list) and not recovered_failure
                else []
            )
            relax_selected_total = len(
                _filtered_metric_ids(
                    selected_ids,
                    visible_seq_sources,
                    use_visible_filter=use_visible_filter,
                )
            )

        rows.append(
            {
                "tier": tier_num,
                "design_total": designs_total,
                "source_counts": source_counts,
                "soluprot_total": sol_total,
                "soluprot_passed": sol_passed,
                "soluprot_pass_rate": _safe_ratio(sol_passed, sol_total),
                "af2_candidate_total": af2_candidate_total,
                "af2_selected_total": af2_selected_total,
                "af2_pass_rate": _safe_ratio(af2_selected_total, af2_candidate_total),
                "plddt_median": _median(candidate_plddt),
                "rmsd_median": _median(candidate_rmsd),
                "relax_candidate_total": relax_candidate_total,
                "relax_selected_total": relax_selected_total,
                "relax_pass_rate": _safe_ratio(
                    relax_selected_total, relax_candidate_total
                ),
                "relax_median": _median(candidate_relax),
            }
        )
    rows.sort(key=lambda item: float(item.get("tier") or 0.0))
    return rows


def _as_float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metric_delta(design_value: float | None, wt_value: float | None) -> float | None:
    if design_value is None or wt_value is None:
        return None
    return float(design_value - wt_value)


def _design_rmsd_values_for_wt_compare(
    design_metrics: dict[str, object],
) -> list[float]:
    target_rmsd_raw = (
        design_metrics.get("af2_target_rmsd")
        if isinstance(design_metrics.get("af2_target_rmsd"), list)
        else []
    )
    target_rmsd_values = [
        float(v) for v in target_rmsd_raw if isinstance(v, (int, float))
    ]
    if target_rmsd_values:
        return target_rmsd_values
    rmsd_raw = (
        design_metrics.get("af2_rmsd")
        if isinstance(design_metrics.get("af2_rmsd"), list)
        else []
    )
    return [float(v) for v in rmsd_raw if isinstance(v, (int, float))]


def _build_comparison_summary(
    *,
    run_root: Path,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
) -> dict[str, object]:
    wt_metrics = _load_wt_metrics(run_root)
    hide_target = _should_hide_target_source(summary, run_root=run_root)
    design_metrics = _collect_design_metrics(run_root, summary, hide_target=hide_target)
    source_metrics = _collect_source_metrics(run_root, summary, hide_target=hide_target)
    tier_compare = _collect_tier_compare_metrics(
        run_root, summary, hide_target=hide_target
    )
    diversity = _build_diversity_summary(request=request, summary=summary)

    wt_enabled = bool(request.get("wt_compare")) if isinstance(request, dict) else False
    wt_sol_score: float | None = None
    wt_plddt: float | None = None
    wt_rmsd: float | None = None
    wt_relax: float | None = None

    if isinstance(wt_metrics, dict):
        wt_sol = (
            wt_metrics.get("soluprot")
            if isinstance(wt_metrics.get("soluprot"), dict)
            else None
        )
        wt_af2 = (
            wt_metrics.get("af2") if isinstance(wt_metrics.get("af2"), dict) else None
        )
        wt_relax_metric = (
            wt_metrics.get("relax")
            if isinstance(wt_metrics.get("relax"), dict)
            else None
        )
        if isinstance(wt_sol, dict) and not wt_sol.get("skipped"):
            wt_sol_score = _as_float_or_none(wt_sol.get("score"))
        if isinstance(wt_af2, dict) and not wt_af2.get("skipped"):
            wt_plddt = _as_float_or_none(wt_af2.get("best_plddt"))
            wt_rmsd = _as_float_or_none(wt_af2.get("rmsd_ca"))
        if isinstance(wt_relax_metric, dict) and not wt_relax_metric.get("skipped"):
            wt_relax = _as_float_or_none(wt_relax_metric.get("score_per_residue"))

    sol_scores_raw = (
        design_metrics.get("soluprot_scores")
        if isinstance(design_metrics.get("soluprot_scores"), list)
        else []
    )
    sol_scores = [float(v) for v in sol_scores_raw if isinstance(v, (int, float))]
    design_sol_median = _median(sol_scores) if sol_scores else None
    sol_total = int(design_metrics.get("soluprot_total") or 0)
    sol_passed = int(design_metrics.get("soluprot_passed") or 0)
    af2_candidate_total = int(design_metrics.get("af2_candidate_total") or 0)
    af2_selected_total = int(design_metrics.get("af2_selected_total") or 0)
    relax_candidate_total = int(design_metrics.get("relax_candidate_total") or 0)
    relax_selected_total = int(design_metrics.get("relax_selected_total") or 0)

    plddt_raw = (
        design_metrics.get("af2_plddt")
        if isinstance(design_metrics.get("af2_plddt"), list)
        else []
    )
    plddt_values = [float(v) for v in plddt_raw if isinstance(v, (int, float))]
    design_plddt_median = _median(plddt_values) if plddt_values else None

    rmsd_raw = (
        design_metrics.get("af2_rmsd")
        if isinstance(design_metrics.get("af2_rmsd"), list)
        else []
    )
    rmsd_values = [float(v) for v in rmsd_raw if isinstance(v, (int, float))]
    wt_compare_rmsd_values = _design_rmsd_values_for_wt_compare(design_metrics)
    design_rmsd_median = _median(rmsd_values) if rmsd_values else None
    wt_compare_rmsd_median = (
        _median(wt_compare_rmsd_values) if wt_compare_rmsd_values else None
    )
    relax_raw = (
        design_metrics.get("relax_score_per_residue")
        if isinstance(design_metrics.get("relax_score_per_residue"), list)
        else []
    )
    relax_values = [float(v) for v in relax_raw if isinstance(v, (int, float))]
    design_relax_median = _median(relax_values) if relax_values else None

    wt_vs_design: dict[str, object] = {
        "soluprot": {
            "wt": wt_sol_score,
            "design_median": design_sol_median,
            "delta_design_minus_wt": _metric_delta(design_sol_median, wt_sol_score),
            "design_total": sol_total,
            "design_passed": sol_passed,
            "design_pass_rate": (float(sol_passed) / float(sol_total))
            if sol_total > 0
            else None,
        },
        "plddt": {
            "wt": wt_plddt,
            "design_median": design_plddt_median,
            "delta_design_minus_wt": _metric_delta(design_plddt_median, wt_plddt),
            "design_total": af2_candidate_total,
        },
        "rmsd": {
            "wt": wt_rmsd,
            "design_median": wt_compare_rmsd_median,
            "delta_design_minus_wt": _metric_delta(wt_compare_rmsd_median, wt_rmsd),
            "design_total": af2_candidate_total,
        },
        "relax": {
            "wt": wt_relax,
            "design_median": design_relax_median,
            "delta_design_minus_wt": _metric_delta(design_relax_median, wt_relax),
            "design_total": relax_candidate_total,
            "design_selected": relax_selected_total,
            "design_pass_rate": _safe_ratio(
                relax_selected_total, relax_candidate_total
            ),
        },
    }

    source_compare: dict[str, dict[str, object]] = {}
    funnel_by_source: dict[str, dict[str, object]] = {}
    requested_total = 0
    observed_total = 0
    materialized_total = 0
    for source_key in ("rfd3", "bioemu", "other"):
        bucket = source_metrics.get(source_key)
        if not isinstance(bucket, dict):
            continue
        source_sol_scores = (
            bucket.get("soluprot_scores")
            if isinstance(bucket.get("soluprot_scores"), list)
            else []
        )
        source_plddt_values = (
            bucket.get("af2_candidate_plddt")
            if isinstance(bucket.get("af2_candidate_plddt"), list)
            else []
        )
        source_rmsd_values = (
            bucket.get("af2_candidate_rmsd")
            if isinstance(bucket.get("af2_candidate_rmsd"), list)
            else []
        )
        source_relax_values = (
            bucket.get("relax_candidate_score_per_residue")
            if isinstance(bucket.get("relax_candidate_score_per_residue"), list)
            else []
        )
        sol_total_src = int(bucket.get("soluprot_total") or 0)
        sol_passed_src = int(bucket.get("soluprot_passed") or 0)
        af2_candidates_src = int(bucket.get("af2_candidate_total") or 0)
        af2_selected_src = int(bucket.get("af2_selected_total") or 0)
        relax_candidates_src = int(bucket.get("relax_candidate_total") or 0)
        relax_selected_src = int(bucket.get("relax_selected_total") or 0)
        requested_src = int(bucket.get("requested_count") or 0)
        observed_src = int(bucket.get("observed_count") or 0)
        materialized_src = int(bucket.get("materialized_count") or 0)
        propagated_src = int(bucket.get("propagated_count") or 0)
        backbone_src = (
            propagated_src
            if propagated_src > 0
            else int(bucket.get("backbone_count") or 0)
        )
        propagation_mode = str(bucket.get("propagation_mode") or "").strip()
        selected_backbone_id = (
            str(bucket.get("selected_backbone_id") or "").strip() or None
        )
        source_compare[source_key] = {
            "backbone_count": backbone_src,
            "requested_count": requested_src,
            "observed_count": observed_src,
            "materialized_count": materialized_src,
            "propagated_count": propagated_src,
            "propagation_mode": propagation_mode,
            "soluprot_total": sol_total_src,
            "soluprot_passed": sol_passed_src,
            "soluprot_pass_rate": _safe_ratio(sol_passed_src, sol_total_src),
            "soluprot_median": _median(
                [float(v) for v in source_sol_scores if isinstance(v, (int, float))]
            ),
            "af2_candidate_total": af2_candidates_src,
            "af2_selected_total": af2_selected_src,
            "af2_pass_rate": _safe_ratio(af2_selected_src, af2_candidates_src),
            "plddt_median": _median(
                [float(v) for v in source_plddt_values if isinstance(v, (int, float))]
            ),
            "rmsd_median": _median(
                [float(v) for v in source_rmsd_values if isinstance(v, (int, float))]
            ),
            "relax_candidate_total": relax_candidates_src,
            "relax_selected_total": relax_selected_src,
            "relax_pass_rate": _safe_ratio(relax_selected_src, relax_candidates_src),
            "relax_median": _median(
                [float(v) for v in source_relax_values if isinstance(v, (int, float))]
            ),
        }
        if selected_backbone_id:
            source_compare[source_key]["selected_backbone_id"] = selected_backbone_id
        funnel_by_source[source_key] = {
            "backbone_count": backbone_src,
            "requested_count": requested_src,
            "observed_count": observed_src,
            "materialized_count": materialized_src,
            "propagated_count": propagated_src,
            "propagation_mode": propagation_mode,
            "soluprot_total": sol_total_src,
            "soluprot_passed": sol_passed_src,
            "soluprot_pass_rate": _safe_ratio(sol_passed_src, sol_total_src),
            "af2_candidate_total": af2_candidates_src,
            "af2_selected_total": af2_selected_src,
            "af2_pass_rate": _safe_ratio(af2_selected_src, af2_candidates_src),
            "relax_candidate_total": relax_candidates_src,
            "relax_selected_total": relax_selected_src,
            "relax_pass_rate": _safe_ratio(relax_selected_src, relax_candidates_src),
            "retention_backbone_to_soluprot_passed": _safe_ratio(
                min(sol_passed_src, backbone_src),
                backbone_src,
            ),
            "retention_backbone_to_af2_selected": _safe_ratio(
                min(af2_selected_src, backbone_src),
                backbone_src,
            ),
            "retention_af2_selected_to_relax_selected": _safe_ratio(
                min(relax_selected_src, af2_selected_src),
                af2_selected_src,
            ),
        }
        if selected_backbone_id:
            funnel_by_source[source_key]["selected_backbone_id"] = selected_backbone_id
        requested_total += requested_src
        observed_total += observed_src
        materialized_total += materialized_src

    backbone_total = 0
    for source_key in ("rfd3", "bioemu", "other"):
        bucket = source_metrics.get(source_key)
        if isinstance(bucket, dict):
            propagated_count = int(bucket.get("propagated_count") or 0)
            backbone_total += (
                propagated_count
                if propagated_count > 0
                else int(bucket.get("backbone_count") or 0)
            )

    funnel = {
        "overall": {
            "backbone_count": backbone_total,
            "requested_count": requested_total,
            "observed_count": observed_total,
            "materialized_count": materialized_total,
            "propagated_count": backbone_total,
            "soluprot_total": sol_total,
            "soluprot_passed": sol_passed,
            "soluprot_pass_rate": _safe_ratio(sol_passed, sol_total),
            "af2_candidate_total": af2_candidate_total,
            "af2_selected_total": af2_selected_total,
            "af2_pass_rate": _safe_ratio(af2_selected_total, af2_candidate_total),
            "relax_candidate_total": relax_candidate_total,
            "relax_selected_total": relax_selected_total,
            "relax_pass_rate": _safe_ratio(relax_selected_total, relax_candidate_total),
            "retention_backbone_to_soluprot_passed": _safe_ratio(
                min(sol_passed, backbone_total),
                backbone_total,
            ),
            "retention_backbone_to_af2_selected": _safe_ratio(
                min(af2_selected_total, backbone_total),
                backbone_total,
            ),
            "retention_af2_selected_to_relax_selected": _safe_ratio(
                min(relax_selected_total, af2_selected_total),
                af2_selected_total,
            ),
        },
        "by_source": funnel_by_source,
    }

    distributions = {
        "soluprot": _distribution_stats(sol_scores),
        "plddt": _distribution_stats(plddt_values),
        "rmsd": _distribution_stats(rmsd_values),
        "relax": _distribution_stats(relax_values),
    }

    return {
        "version": 6,
        "generated_at": _now_iso(),
        "wt_compare_enabled": wt_enabled,
        "wt_vs_design": wt_vs_design,
        "source_compare": source_compare,
        "funnel": funnel,
        "tier_compare": tier_compare,
        "distributions": distributions,
        "diversity": diversity,
    }


def _ascii_bar(value: float, *, max_value: float, width: int = 16) -> str:
    if max_value <= 0:
        return "[" + ("." * width) + "]"
    ratio = max(0.0, min(float(value) / float(max_value), 1.0))
    filled = int(round(ratio * width))
    filled = max(0, min(width, filled))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def _load_wt_metrics(run_root: Path) -> dict[str, object] | None:
    return _load_json_file(run_root / "wt" / "metrics.json")


def _load_mask_consensus(run_root: Path) -> dict[str, object] | None:
    return _load_json_file(run_root / "mask_consensus.json")


def _normalize_positions(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for pos in raw:
        try:
            out.append(int(pos))
        except Exception:
            continue
    return sorted(set(out))


def _normalize_chain_positions(raw: object) -> dict[str, list[int]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[int]] = {}
    for chain, positions in raw.items():
        cleaned = _normalize_positions(positions)
        if cleaned:
            out[str(chain)] = cleaned
    return out


def _format_positions_preview(positions: list[int], *, limit: int = 8) -> str:
    if not positions:
        return "none"
    preview = ", ".join(str(p) for p in positions[:limit])
    if len(positions) > limit:
        preview += f", ...(+{len(positions) - limit})"
    return preview


def _format_chain_counts(positions_by_chain: dict[str, list[int]]) -> str:
    if not positions_by_chain:
        return "none"
    parts = [f"{chain}={len(pos)}" for chain, pos in sorted(positions_by_chain.items())]
    return ", ".join(parts) if parts else "none"


def _sort_tier_keys(keys: list[str]) -> list[str]:
    def _key(val: str) -> tuple[int, float | str]:
        try:
            return (0, float(val))
        except Exception:
            return (1, str(val))

    return sorted({str(k) for k in keys if str(k).strip()}, key=_key)


def _extract_runpod_job_ids(payload: dict[str, object]) -> list[str]:
    job_ids: list[str] = []
    job_id = payload.get("job_id")
    if isinstance(job_id, str) and job_id.strip():
        job_ids.append(job_id.strip())
    jobs = payload.get("jobs")
    if isinstance(jobs, dict):
        for val in jobs.values():
            if isinstance(val, str) and val.strip():
                job_ids.append(val.strip())
    return job_ids


def _extract_runpod_endpoint_id(payload: dict[str, object]) -> str | None:
    raw = payload.get("endpoint_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _collect_runpod_jobs(run_root: Path) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    af2_target = run_root / "af2_target_runpod_job.json"
    if af2_target.exists():
        payload = _load_json_file(af2_target)
        if isinstance(payload, dict):
            endpoint_id = _extract_runpod_endpoint_id(payload)
            for job_id in _extract_runpod_job_ids(payload):
                entry = {"kind": "af2", "job_id": job_id, "path": str(af2_target)}
                if endpoint_id:
                    entry["endpoint_id"] = endpoint_id
                jobs.append(entry)

    for path in run_root.rglob("runpod_job.json"):
        rel = path.relative_to(run_root)
        parts = rel.parts
        kind = "unknown"
        if "af2" in parts:
            kind = "af2"
        elif parts and parts[0] == "msa":
            kind = "mmseqs"
        elif parts and parts[0] == "rfd3":
            kind = "rfd3"
        elif parts and parts[0] == "diffdock":
            kind = "diffdock"
        elif "tiers" in parts:
            kind = "proteinmpnn"
        payload = _load_json_file(path)
        if isinstance(payload, dict):
            endpoint_id = _extract_runpod_endpoint_id(payload)
            for job_id in _extract_runpod_job_ids(payload):
                entry = {"kind": kind, "job_id": job_id, "path": str(path)}
                if endpoint_id:
                    entry["endpoint_id"] = endpoint_id
                jobs.append(entry)

    for path in run_root.rglob("runpod_jobs.json"):
        rel = path.relative_to(run_root)
        parts = rel.parts
        kind = "af2" if "af2" in parts else "unknown"
        payload = _load_json_file(path)
        if isinstance(payload, dict):
            endpoint_id = _extract_runpod_endpoint_id(payload)
            for job_id in _extract_runpod_job_ids(payload):
                entry = {"kind": kind, "job_id": job_id, "path": str(path)}
                if endpoint_id:
                    entry["endpoint_id"] = endpoint_id
                jobs.append(entry)

    return jobs


def _runpod_admin_service(runner: PipelineRunner):
    service = build_runpod_admin_service(runner)
    if service is None:
        raise ValueError(
            "RunPod admin is unavailable: no RunPod-backed endpoints are configured"
        )
    return service


def _runpod_list_endpoints_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    service = _runpod_admin_service(runner)
    include_workers = _as_bool(arguments.get("include_workers"), False)
    managed_only = _as_bool(arguments.get("managed_only"), False)
    result = service.list_endpoints(include_workers=include_workers)
    endpoints = (
        result.get("endpoints") if isinstance(result.get("endpoints"), list) else []
    )
    visible = [
        item
        for item in endpoints
        if isinstance(item, dict) and (not managed_only or bool(item.get("managed")))
    ]
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    visible_summary = {
        **summary,
        "visible_endpoints": len(visible),
        "visible_managed_endpoints": sum(1 for item in visible if item.get("managed")),
    }
    return {
        **result,
        "endpoints": visible,
        "filters": {"managed_only": managed_only, "include_workers": include_workers},
        "visible_summary": visible_summary,
    }


def _runpod_get_endpoint_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    service = _runpod_admin_service(runner)
    endpoint_id = str(arguments.get("endpoint_id") or "").strip()
    if not endpoint_id:
        raise ValueError("endpoint_id is required")
    include_workers = _as_bool(arguments.get("include_workers"), True)
    return service.get_endpoint(endpoint_id, include_workers=include_workers)


def _runpod_update_endpoint_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    service = _runpod_admin_service(runner)
    endpoint_id = str(arguments.get("endpoint_id") or "").strip()
    if not endpoint_id:
        raise ValueError("endpoint_id is required")
    patch = sanitize_runpod_endpoint_patch(arguments.get("patch"))
    return service.update_endpoint(endpoint_id, patch)


def _runpod_list_billing_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    service = _runpod_admin_service(runner)
    endpoint_id = str(arguments.get("endpoint_id") or "").strip() or None
    days = _as_int(arguments.get("days"), 7)
    bucket_size = str(arguments.get("bucket_size") or "day").strip().lower() or "day"
    start_time = str(arguments.get("start_time") or "").strip() or None
    end_time = str(arguments.get("end_time") or "").strip() or None
    return service.list_billing(
        endpoint_id=endpoint_id,
        days=max(days, 1),
        bucket_size=bucket_size,
        start_time=start_time,
        end_time=end_time,
    )


def _runpod_get_history_tool(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    service = _runpod_admin_service(runner)
    endpoint_id = str(arguments.get("endpoint_id") or "").strip() or None
    days = _as_int(arguments.get("days"), 7)
    usage_resolution = (
        str(arguments.get("usage_resolution") or "auto").strip().lower() or "auto"
    )
    billing_resolution = (
        str(arguments.get("billing_resolution") or "auto").strip().lower() or "auto"
    )
    start_time = str(arguments.get("start_time") or "").strip() or None
    end_time = str(arguments.get("end_time") or "").strip() or None
    limit = _as_int(arguments.get("limit"), 120)
    return service.get_history(
        endpoint_id=endpoint_id,
        days=max(days, 1),
        usage_resolution=usage_resolution,
        billing_resolution=billing_resolution,
        start_time=start_time,
        end_time=end_time,
        limit=max(limit, 1),
    )


def _model_provider_store(runner: PipelineRunner):
    return model_provider_store_from_env(getattr(runner, "output_root", None))


def _model_provider_user(arguments: dict[str, Any]) -> dict[str, Any]:
    return arguments.get("user") if isinstance(arguments.get("user"), dict) else {}


def _model_provider_scope(arguments: dict[str, Any], user: dict[str, Any]) -> str:
    provider = arguments.get("provider") if isinstance(arguments.get("provider"), dict) else {}
    raw_scope = arguments.get("scope", provider.get("scope"))
    if raw_scope is None:
        role = str(user.get("role") or "")
        return "global" if role in {"admin", "model_manager"} or not user else "user"
    scope = str(raw_scope or "global").strip().lower().replace("-", "_")
    if scope in {"global", "default", "admin"}:
        return "global"
    if scope in {"user", "personal", "mine"}:
        return "user"
    raise ValueError("scope must be one of: global, user")


def _model_provider_user_id(user: dict[str, Any]) -> str:
    return str(user.get("username") or "").strip()


def _can_manage_global_model_providers(user: dict[str, Any]) -> bool:
    return str(user.get("role") or "") in {"admin", "model_manager"}


def _model_provider_list_tool(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    store = _model_provider_store(runner)
    include_health = _as_bool(arguments.get("include_health"), False)
    user = _model_provider_user(arguments)
    scope = _model_provider_scope(arguments, user)
    user_id = _model_provider_user_id(user) if scope == "user" else None
    providers = build_provider_summary(store, user_id=user_id)
    health: dict[str, Any] = {}
    if include_health:
        for provider in providers:
            model_key = str(provider.get("model_key") or "")
            if model_key:
                health[model_key] = store.health(model_key, user_id=user_id)
    return {
        "providers": providers,
        "health": health,
        "scope": scope,
        "can_manage_global": _can_manage_global_model_providers(user),
    }


def _model_provider_update_tool(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    store = _model_provider_store(runner)
    model_key = str(arguments.get("model_key") or "").strip()
    provider = arguments.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("provider must be an object")
    user = _model_provider_user(arguments)
    scope = _model_provider_scope(arguments, user)
    user_id = _model_provider_user_id(user) if scope == "user" else None
    if scope == "global" and user and not _can_manage_global_model_providers(user):
        raise AuthError("model manager required")
    if scope == "user" and not user_id:
        raise AuthError("user required")
    actor = str(user.get("username") or "")
    return {"provider": store.upsert(model_key, provider, actor=actor, scope=scope, user_id=user_id), "scope": scope}


def _model_provider_health_tool(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    store = _model_provider_store(runner)
    model_key = str(arguments.get("model_key") or "").strip()
    if not model_key:
        raise ValueError("model_key is required")
    provider = arguments.get("provider")
    user = _model_provider_user(arguments)
    scope = _model_provider_scope(arguments, user)
    user_id = _model_provider_user_id(user) if scope == "user" else None
    return store.health(model_key, provider if isinstance(provider, dict) else None, user_id=user_id)


def _cath_get_batch_overview_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    item_limit = max(1, _as_int(arguments.get("item_limit"), 200))
    return summarize_all_subsets(runner.output_root, item_limit=item_limit)


def _cath_launch_batch_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    subset = str(arguments.get("subset") or "").strip().lower()
    keep_local = _as_bool(arguments.get("keep_local"), False)
    stop_on_error = _as_bool(arguments.get("stop_on_error"), False)
    max_workers = arguments.get("max_workers")
    launch = launch_cath_batch_job(
        runner.output_root,
        subset=subset,
        keep_local=keep_local,
        stop_on_error=stop_on_error,
        max_workers=(
            max(1, int(max_workers))
            if isinstance(max_workers, (int, float)) or str(max_workers or "").strip()
            else None
        ),
    )
    return {"job": launch}


def _cath_launch_training_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    raw_subsets = arguments.get("subsets")
    subsets: list[str] = []
    if isinstance(raw_subsets, list):
        subsets = [
            str(item).strip().lower() for item in raw_subsets if str(item).strip()
        ]
    elif raw_subsets is not None:
        subsets = [str(raw_subsets).strip().lower()]
    launch = launch_cath_training_job(runner.output_root, subsets=subsets)
    return {"job": launch}


def _cath_list_jobs_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    kind = str(arguments.get("kind") or "").strip()
    limit = max(1, _as_int(arguments.get("limit"), 20))
    normalized_kind = kind or None
    if normalized_kind == "batch":
        normalized_kind = job_kind_batch()
    elif normalized_kind == "train":
        normalized_kind = job_kind_train()
    jobs = list_managed_jobs(runner.output_root, kind=normalized_kind, limit=limit)
    return {"jobs": jobs}


def _cath_get_job_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    return {"job": read_managed_job(runner.output_root, job_id)}


def _cath_read_job_log_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    max_bytes = max(1, _as_int(arguments.get("max_bytes"), 120_000))
    return read_managed_job_log(runner.output_root, job_id, max_bytes=max_bytes)


def _cath_stop_job_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    return stop_managed_job(runner.output_root, job_id)


def _cath_delete_job_tool(
    runner: PipelineRunner,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    return delete_managed_job(runner.output_root, job_id)


def _client_cancel_info(client: object | None) -> tuple[object, str] | None:
    if client is None:
        return None
    runpod = getattr(client, "runpod", None)
    endpoint_id = getattr(client, "endpoint_id", None)
    if runpod is None or endpoint_id is None:
        return None
    if not hasattr(runpod, "cancel"):
        return None
    endpoint = str(endpoint_id).strip()
    if not endpoint:
        return None
    return runpod, endpoint


def _score_payload(
    feedback_counts: dict[str, object],
    experiment_counts: dict[str, object],
) -> dict[str, object]:
    return compute_score(feedback_counts, experiment_counts)


def _mask_consensus_report_lines(
    *,
    run_root: Path,
    request: dict[str, object] | None,
    lang: str = "en",
) -> list[str]:
    payload = _load_mask_consensus(run_root)
    enabled = bool(request.get("mask_consensus_apply")) if request else False
    if payload is None and not enabled:
        return []

    is_ko = str(lang).lower().startswith("ko")
    none_label = "없음" if is_ko else "none"
    lines: list[str] = []
    lines.append("## 마스킹 합의" if is_ko else "## Mask Consensus")
    lines.append(
        f"- ProteinMPNN 적용 여부: {'yes' if enabled else 'no'}"
        if is_ko
        else f"- Applied to ProteinMPNN: {'yes' if enabled else 'no'}"
    )

    if payload is None:
        lines.append(
            "- 마스킹 합의 데이터가 아직 없습니다."
            if is_ko
            else "- Mask consensus data not available yet."
        )
        lines.append("")
        return lines

    consensus = payload.get("consensus") if isinstance(payload, dict) else None
    if not isinstance(consensus, dict):
        lines.append(
            "- 마스킹 합의 데이터가 올바르지 않습니다."
            if is_ko
            else "- Mask consensus data invalid."
        )
        lines.append("")
        return lines

    threshold = consensus.get("threshold")
    if threshold is not None:
        label = "합의 기준(표)" if is_ko else "Vote threshold"
        lines.append(f"- {label}: {threshold}")

    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    for note in notes:
        if not isinstance(note, str) or not note.strip():
            continue
        label = "참고" if is_ko else "Note"
        lines.append(f"- {label}: {note.strip()}")

    fixed_query = (
        consensus.get("fixed_positions_query_by_tier")
        if isinstance(consensus.get("fixed_positions_query_by_tier"), dict)
        else {}
    )
    fixed_by_tier = (
        consensus.get("fixed_positions_by_tier")
        if isinstance(consensus.get("fixed_positions_by_tier"), dict)
        else {}
    )

    tier_keys = _sort_tier_keys(list(fixed_query.keys()) + list(fixed_by_tier.keys()))
    if not tier_keys:
        lines.append(
            "- 서열 보존율별 합의: 없음"
            if is_ko
            else "- Sequence-conservation consensus: none"
        )
        lines.append("")
        return lines

    lines.append(
        "- 서열 보존율별 합의:" if is_ko else "- Sequence-conservation consensus:"
    )
    for tier_key in tier_keys:
        query_positions = _normalize_positions(fixed_query.get(tier_key))
        chain_positions = _normalize_chain_positions(fixed_by_tier.get(tier_key))
        applied_positions: dict[str, list[int]] = {}
        if enabled:
            applied_payload = _load_json_file(
                run_root / "tiers" / str(tier_key) / "fixed_positions.json"
            )
            applied_positions = _normalize_chain_positions(applied_payload)

        segments: list[str] = []
        if query_positions:
            preview = _format_positions_preview(query_positions)
            if preview == "none":
                preview = none_label
            if is_ko:
                segments.append(f"query 고정={len(query_positions)} ({preview})")
            else:
                segments.append(f"query_fixed={len(query_positions)} ({preview})")
        else:
            segments.append("query 고정=0" if is_ko else "query_fixed=0")

        if chain_positions:
            chain_counts = _format_chain_counts(chain_positions)
            if chain_counts == "none":
                chain_counts = none_label
            if is_ko:
                segments.append(f"체인 합의={chain_counts}")
            else:
                segments.append(f"consensus_chain={chain_counts}")

        if enabled and applied_positions:
            applied_counts = _format_chain_counts(applied_positions)
            if applied_counts == "none":
                applied_counts = none_label
            if is_ko:
                segments.append(f"적용 고정(ProteinMPNN)={applied_counts}")
            else:
                segments.append(f"applied_fixed={applied_counts}")

        if not segments:
            segments.append("데이터 없음" if is_ko else "no data")
        lines.append(
            f"  - {_format_conservation_tier_label(tier_key, lang=lang)}: "
            + "; ".join(segments)
        )
    lines.append("")
    return lines


def _append_wt_visual_lines(
    lines: list[str],
    *,
    wt_sol_score: float | None,
    design_sol_median: float | None,
    wt_plddt: float | None,
    design_plddt_median: float | None,
    wt_rmsd: float | None,
    design_rmsd_median: float | None,
    lang: str = "en",
) -> None:
    is_ko = str(lang).lower().startswith("ko")
    snapshots: list[str] = []
    if wt_sol_score is not None and design_sol_median is not None:
        max_sol = max(1.0, wt_sol_score, design_sol_median)
        snapshots.append(
            (
                f"SoluProt WT {_ascii_bar(wt_sol_score, max_value=max_sol)} {wt_sol_score:.3f} | "
                f"Design {_ascii_bar(design_sol_median, max_value=max_sol)} {design_sol_median:.3f}"
            )
        )
    if wt_plddt is not None and design_plddt_median is not None:
        snapshots.append(
            (
                f"pLDDT WT {_ascii_bar(wt_plddt, max_value=100.0)} {wt_plddt:.1f} | "
                f"Design {_ascii_bar(design_plddt_median, max_value=100.0)} {design_plddt_median:.1f}"
            )
        )
    if wt_rmsd is not None and design_rmsd_median is not None:
        quality_wt = max(0.0, 1.0 - (wt_rmsd / 5.0))
        quality_design = max(0.0, 1.0 - (design_rmsd_median / 5.0))
        snapshots.append(
            (
                f"RMSD(lower better) WT {_ascii_bar(quality_wt, max_value=1.0)} {wt_rmsd:.2f} | "
                f"Design {_ascii_bar(quality_design, max_value=1.0)} {design_rmsd_median:.2f}"
            )
        )
    if not snapshots:
        return
    lines.append("- 시각 요약:" if is_ko else "- Visual snapshot:")
    for row in snapshots:
        lines.append(f"  - {row}")


def _propagation_mode_label(mode: object, *, lang: str = "en") -> str:
    raw = str(mode or "").strip().lower()
    is_ko = str(lang).lower().startswith("ko")
    labels = {
        "none": ("not used", "미사용"),
        "propagated_only": ("used", "사용"),
        "all_materialized": ("all saved used", "저장 구조 전체 사용"),
        "all_observed": ("all observed used", "관측 구조 전체 사용"),
        "selected_only": ("selected representative only", "대표 1개만 사용"),
        "partial": ("partially used", "일부만 사용"),
    }
    pair = labels.get(raw)
    if pair is None:
        return raw
    return pair[1] if is_ko else pair[0]


def _source_usage_summary_text(
    source: str, bucket: dict[str, object], *, lang: str = "en"
) -> str | None:
    if not isinstance(bucket, dict):
        return None
    is_ko = str(lang).lower().startswith("ko")
    requested = int(bucket.get("requested_count") or 0)
    observed = int(bucket.get("observed_count") or 0)
    materialized = int(bucket.get("materialized_count") or 0)
    used = int(bucket.get("propagated_count") or bucket.get("backbone_count") or 0)
    mode = _propagation_mode_label(bucket.get("propagation_mode"), lang=lang)
    selected = str(bucket.get("selected_backbone_id") or "").strip()
    if (
        requested <= 0
        and observed <= 0
        and materialized <= 0
        and used <= 0
        and not mode
        and not selected
    ):
        return None
    source_name = (
        "RFD3"
        if source == "rfd3"
        else "BioEmu"
        if source == "bioemu"
        else ("기타" if is_ko else "Other")
    )
    counts_text = (
        f"요청 {requested} · 관측 {observed} · 저장 {materialized} · 사용 {used}"
        if is_ko
        else f"requested {requested} · observed {observed} · saved {materialized} · used {used}"
    )
    suffixes: list[str] = []
    if mode:
        suffixes.append(mode)
    if selected:
        suffixes.append(f"selected {selected}" if not is_ko else f"대표 {selected}")
    detail = f" ({'; '.join(suffixes)})" if suffixes else ""
    return f"{source_name}: {counts_text}{detail}"


def _append_source_comparison_lines(
    lines: list[str],
    *,
    source_metrics: dict[str, dict[str, object]],
    lang: str = "en",
) -> None:
    is_ko = str(lang).lower().startswith("ko")
    source_names = {
        "rfd3": "RFD3",
        "bioemu": "BioEmu",
        "other": ("기타" if is_ko else "Other"),
    }
    ordered_sources = ["rfd3", "bioemu", "other"]
    rows: list[
        tuple[
            str,
            dict[str, object],
            int,
            int,
            int,
            int,
            int,
            float | None,
            float | None,
            float | None,
        ]
    ] = []
    for source in ordered_sources:
        bucket = source_metrics.get(source)
        if not isinstance(bucket, dict):
            continue
        backbone_count = int(bucket.get("backbone_count") or 0)
        sol_total = int(bucket.get("soluprot_total") or 0)
        sol_passed = int(bucket.get("soluprot_passed") or 0)
        af2_candidate_total = int(bucket.get("af2_candidate_total") or 0)
        af2_selected_total = int(bucket.get("af2_selected_total") or 0)
        sol_scores = (
            bucket.get("soluprot_scores")
            if isinstance(bucket.get("soluprot_scores"), list)
            else []
        )
        plddt_vals = (
            bucket.get("af2_candidate_plddt")
            if isinstance(bucket.get("af2_candidate_plddt"), list)
            else []
        )
        rmsd_vals = (
            bucket.get("af2_candidate_rmsd")
            if isinstance(bucket.get("af2_candidate_rmsd"), list)
            else []
        )
        if (
            backbone_count <= 0
            and sol_total <= 0
            and sol_passed <= 0
            and af2_candidate_total <= 0
            and af2_selected_total <= 0
            and not sol_scores
            and not plddt_vals
            and not rmsd_vals
        ):
            continue
        rows.append(
            (
                source,
                bucket,
                backbone_count,
                sol_total,
                sol_passed,
                af2_candidate_total,
                af2_selected_total,
                _median([float(x) for x in sol_scores if isinstance(x, (int, float))])
                if sol_scores
                else None,
                _median([float(x) for x in plddt_vals if isinstance(x, (int, float))])
                if plddt_vals
                else None,
                _median([float(x) for x in rmsd_vals if isinstance(x, (int, float))])
                if rmsd_vals
                else None,
            )
        )

    if not rows:
        return

    lines.append(
        "## 백본 소스 비교 (RFD3 vs BioEmu)"
        if is_ko
        else "## Backbone Source Comparison (RFD3 vs BioEmu)"
    )
    lines.append(
        "| Source | Backbones | SoluProt pass | Median SoluProt | ColabFold selected/candidates | Median pLDDT | Median RMSD |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for (
        source,
        _bucket,
        backbone_count,
        sol_total,
        sol_passed,
        af2_candidate_total,
        af2_selected_total,
        sol_med,
        plddt_med,
        rmsd_med,
    ) in rows:
        pass_rate = (sol_passed / sol_total) if sol_total else None
        pass_text = (
            f"{sol_passed}/{sol_total} ({pass_rate:.1%})"
            if pass_rate is not None
            else "-"
        )
        sol_text = f"{sol_med:.3f}" if sol_med is not None else "-"
        plddt_text = f"{plddt_med:.1f}" if plddt_med is not None else "-"
        rmsd_text = f"{rmsd_med:.2f}" if rmsd_med is not None else "-"
        af2_text = (
            f"{af2_selected_total}/{af2_candidate_total}"
            if af2_candidate_total > 0
            else str(af2_selected_total)
        )
        lines.append(
            f"| {source_names.get(source, source)} | {backbone_count} | {pass_text} | {sol_text} | {af2_text} | {plddt_text} | {rmsd_text} |"
        )

    usage_rows = []
    for source, bucket, *_rest in rows:
        usage = _source_usage_summary_text(source, bucket, lang=lang)
        if usage:
            usage_rows.append(usage)
    if usage_rows:
        lines.append("- Backbone generation/use:" if not is_ko else "- 백본 생성/사용:")
        for usage in usage_rows:
            lines.append(f"  - {usage}")

    rfd3_bucket = (
        source_metrics.get("rfd3")
        if isinstance(source_metrics.get("rfd3"), dict)
        else {}
    )
    bioemu_bucket = (
        source_metrics.get("bioemu")
        if isinstance(source_metrics.get("bioemu"), dict)
        else {}
    )
    rfd3_total = (
        int(rfd3_bucket.get("soluprot_total") or 0)
        if isinstance(rfd3_bucket, dict)
        else 0
    )
    bioemu_total = (
        int(bioemu_bucket.get("soluprot_total") or 0)
        if isinstance(bioemu_bucket, dict)
        else 0
    )
    rfd3_passed = (
        int(rfd3_bucket.get("soluprot_passed") or 0)
        if isinstance(rfd3_bucket, dict)
        else 0
    )
    bioemu_passed = (
        int(bioemu_bucket.get("soluprot_passed") or 0)
        if isinstance(bioemu_bucket, dict)
        else 0
    )
    rfd3_af2 = (
        int(rfd3_bucket.get("af2_selected_total") or 0)
        if isinstance(rfd3_bucket, dict)
        else 0
    )
    bioemu_af2 = (
        int(bioemu_bucket.get("af2_selected_total") or 0)
        if isinstance(bioemu_bucket, dict)
        else 0
    )
    if rfd3_total > 0 or bioemu_total > 0:
        rfd3_rate = (rfd3_passed / rfd3_total) if rfd3_total else 0.0
        bioemu_rate = (bioemu_passed / bioemu_total) if bioemu_total else 0.0
        lines.append("- SoluProt 통과율 바:" if is_ko else "- SoluProt pass-rate bars:")
        lines.append(f"  - RFD3 {_ascii_bar(rfd3_rate, max_value=1.0)} {rfd3_rate:.1%}")
        lines.append(
            f"  - BioEmu {_ascii_bar(bioemu_rate, max_value=1.0)} {bioemu_rate:.1%}"
        )
    if rfd3_af2 > 0 or bioemu_af2 > 0:
        max_af2 = float(max(rfd3_af2, bioemu_af2, 1))
        lines.append(
            "- ColabFold 선발 개수 바:" if is_ko else "- ColabFold selected-count bars:"
        )
        lines.append(
            f"  - RFD3 {_ascii_bar(float(rfd3_af2), max_value=max_af2)} {rfd3_af2}"
        )
        lines.append(
            f"  - BioEmu {_ascii_bar(float(bioemu_af2), max_value=max_af2)} {bioemu_af2}"
        )
    lines.append("")


def _append_extended_comparison_lines(
    lines: list[str],
    *,
    comparison_summary: dict[str, object] | None,
    lang: str = "en",
) -> None:
    if not isinstance(comparison_summary, dict):
        return
    is_ko = str(lang).lower().startswith("ko")
    funnel = (
        comparison_summary.get("funnel")
        if isinstance(comparison_summary.get("funnel"), dict)
        else {}
    )
    overall = funnel.get("overall") if isinstance(funnel.get("overall"), dict) else {}
    by_source = (
        funnel.get("by_source") if isinstance(funnel.get("by_source"), dict) else {}
    )
    tier_rows = (
        comparison_summary.get("tier_compare")
        if isinstance(comparison_summary.get("tier_compare"), list)
        else []
    )
    distributions = (
        comparison_summary.get("distributions")
        if isinstance(comparison_summary.get("distributions"), dict)
        else {}
    )
    diversity = (
        comparison_summary.get("diversity")
        if isinstance(comparison_summary.get("diversity"), dict)
        else {}
    )
    if not overall and not tier_rows and not distributions and not diversity:
        return

    def _pct(value: object) -> str:
        return f"{float(value):.1%}" if isinstance(value, (int, float)) else "-"

    lines.append(
        "## 확장 비교 하이라이트" if is_ko else "## Extended Comparison Highlights"
    )

    backbones = int(overall.get("backbone_count") or 0)
    sol_passed = int(overall.get("soluprot_passed") or 0)
    sol_total = int(overall.get("soluprot_total") or 0)
    af2_selected = int(overall.get("af2_selected_total") or 0)
    af2_candidates = int(overall.get("af2_candidate_total") or 0)
    if backbones > 0 or sol_total > 0 or af2_candidates > 0:
        lines.append(
            (
                f"- Funnel: backbone={backbones} → SoluProt={sol_passed}/{sol_total} ({_pct(overall.get('soluprot_pass_rate'))})"
                f" → ColabFold={af2_selected}/{af2_candidates} ({_pct(overall.get('af2_pass_rate'))})"
            )
        )
        lines.append(
            (
                f"- Retention from backbone: SoluProt={_pct(overall.get('retention_backbone_to_soluprot_passed'))}, "
                f"ColabFold={_pct(overall.get('retention_backbone_to_af2_selected'))}"
            )
        )

    rows: list[tuple[str, dict[str, object]]] = []
    for key in ("rfd3", "bioemu", "other"):
        bucket = by_source.get(key) if isinstance(by_source, dict) else None
        if isinstance(bucket, dict):
            rows.append((key, bucket))
    if rows:
        lines.append("- Source funnel:" if not is_ko else "- 소스별 Funnel:")
        lines.append("| Source | Backbones | SoluProt pass | ColabFold pass |")
        lines.append("|---|---:|---:|---:|")
        for source, bucket in rows:
            source_name = (
                "RFD3"
                if source == "rfd3"
                else "BioEmu"
                if source == "bioemu"
                else ("기타" if is_ko else "Other")
            )
            sol_txt = f"{int(bucket.get('soluprot_passed') or 0)}/{int(bucket.get('soluprot_total') or 0)} ({_pct(bucket.get('soluprot_pass_rate'))})"
            af2_txt = f"{int(bucket.get('af2_selected_total') or 0)}/{int(bucket.get('af2_candidate_total') or 0)} ({_pct(bucket.get('af2_pass_rate'))})"
            lines.append(
                f"| {source_name} | {int(bucket.get('backbone_count') or 0)} | {sol_txt} | {af2_txt} |"
            )

    if tier_rows:
        lines.append(
            "- Sequence-conservation summary:" if not is_ko else "- 서열 보존율별 요약:"
        )
        lines.append(
            "| Sequence conservation | Designs | SoluProt pass | ColabFold pass | Median pLDDT | Median RMSD |"
            if not is_ko
            else "| 서열 보존율 | Designs | SoluProt pass | ColabFold pass | Median pLDDT | Median RMSD |"
        )
        lines.append("|---:|---:|---:|---:|---:|---:|")
        for row in tier_rows:
            if not isinstance(row, dict):
                continue
            tier_text = _format_conservation_tier_value(row.get("tier"))
            sol_txt = f"{int(row.get('soluprot_passed') or 0)}/{int(row.get('soluprot_total') or 0)} ({_pct(row.get('soluprot_pass_rate'))})"
            af2_txt = f"{int(row.get('af2_selected_total') or 0)}/{int(row.get('af2_candidate_total') or 0)} ({_pct(row.get('af2_pass_rate'))})"
            plddt = row.get("plddt_median")
            rmsd = row.get("rmsd_median")
            plddt_txt = (
                f"{float(plddt):.1f}" if isinstance(plddt, (int, float)) else "-"
            )
            rmsd_txt = f"{float(rmsd):.2f}" if isinstance(rmsd, (int, float)) else "-"
            lines.append(
                f"| {tier_text} | {int(row.get('design_total') or 0)} | {sol_txt} | {af2_txt} | {plddt_txt} | {rmsd_txt} |"
            )

    def _dist_line(name: str, metric: object) -> str | None:
        if not isinstance(metric, dict):
            return None
        return (
            f"- {name}: n={int(metric.get('count') or 0)} "
            f"P10/P50/P90="
            f"{(f'{float(metric.get("p10")):.3f}' if isinstance(metric.get('p10'), (int, float)) else '-')}/"
            f"{(f'{float(metric.get("median")):.3f}' if isinstance(metric.get('median'), (int, float)) else '-')}/"
            f"{(f'{float(metric.get("p90")):.3f}' if isinstance(metric.get('p90'), (int, float)) else '-')}"
        )

    dist_lines = [
        _dist_line("SoluProt", distributions.get("soluprot")),
        _dist_line("pLDDT", distributions.get("plddt")),
        _dist_line("RMSD", distributions.get("rmsd")),
    ]
    dist_lines = [line for line in dist_lines if line]
    if dist_lines:
        lines.append("- Distribution snapshot:" if not is_ko else "- 분포 요약:")
        lines.extend(dist_lines)

    wt_identity = (
        diversity.get("wt_identity")
        if isinstance(diversity.get("wt_identity"), dict)
        else {}
    )
    pairwise = (
        diversity.get("design_pairwise_identity")
        if isinstance(diversity.get("design_pairwise_identity"), dict)
        else {}
    )
    if wt_identity or pairwise:
        lines.append("- Sequence diversity:" if not is_ko else "- 서열 다양성:")
        if wt_identity:
            lines.append(
                f"  - WT identity median={_pct(wt_identity.get('median'))} "
                f"(best={_pct(wt_identity.get('best'))}, worst={_pct(wt_identity.get('worst'))}, n={int(wt_identity.get('count') or 0)})"
            )
        if pairwise:
            lines.append(
                f"  - Design pairwise identity median={_pct(pairwise.get('median'))} "
                f"(pairs={int(pairwise.get('evaluated_pairs') or 0)})"
            )
    lines.append("")


def _compact_error_message(err: object, *, max_chars: int = 220) -> str:
    text = _as_text(err).replace("\r", "\n").strip()
    if not text:
        return "-"
    first_line = ""
    for raw_line in text.split("\n"):
        candidate = raw_line.strip()
        if candidate:
            first_line = candidate
            break
    if not first_line:
        first_line = text
    first_line = re.sub(r"\s+", " ", first_line).strip()
    if len(first_line) > max_chars:
        return first_line[: max(1, max_chars - 1)].rstrip() + "…"
    return first_line


def _format_ratio(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100.0:.1f}%"
    return "-"


def _format_metric(value: object, digits: int) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return "-"


def _normalize_conservation_tier(value: object) -> float | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        num = float(value)
    else:
        return None
    if num <= 0:
        return None
    return num / 100.0 if num > 1.0 else num


def _format_conservation_tier_value(value: object) -> str:
    normalized = _normalize_conservation_tier(value)
    if normalized is None:
        return "-"
    pct = round(normalized * 100.0, 2)
    return f"{pct:.2f}".rstrip("0").rstrip(".") + "%"


def _format_conservation_tier_label(value: object, *, lang: str = "en") -> str:
    tier_text = _format_conservation_tier_value(value)
    if tier_text == "-":
        return "-"
    if str(lang).lower().startswith("ko"):
        return f"서열 보존율 {tier_text}"
    return f"Sequence conservation {tier_text}"


def _format_wt_difference(value: object) -> str:
    if not isinstance(value, dict):
        return "-"
    diff_count = value.get("wt_diff_count")
    compare_len = value.get("wt_compare_len")
    if (
        not isinstance(diff_count, (int, float))
        or not isinstance(compare_len, (int, float))
        or float(compare_len) <= 0
    ):
        return "-"
    identity_pct = value.get("wt_identity_pct")
    if isinstance(identity_pct, (int, float)):
        return f"{int(diff_count)}/{int(compare_len)} (identity {float(identity_pct):.1f}%)"
    return f"{int(diff_count)}/{int(compare_len)}"


def _display_pipeline_stage(value: object | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"novelty", "wt_diff", "wtdiff"}:
        return "WT Diff"
    return str(value or "")


def _append_report_snapshot_lines(
    lines: list[str],
    *,
    comparison_summary: dict[str, object] | None,
    lang: str = "en",
) -> None:
    if not isinstance(comparison_summary, dict):
        return
    is_ko = str(lang).lower().startswith("ko")
    funnel = (
        comparison_summary.get("funnel")
        if isinstance(comparison_summary.get("funnel"), dict)
        else {}
    )
    overall = funnel.get("overall") if isinstance(funnel.get("overall"), dict) else {}
    distributions = (
        comparison_summary.get("distributions")
        if isinstance(comparison_summary.get("distributions"), dict)
        else {}
    )
    wt_vs_design = (
        comparison_summary.get("wt_vs_design")
        if isinstance(comparison_summary.get("wt_vs_design"), dict)
        else {}
    )
    if not overall and not distributions and not wt_vs_design:
        return

    completeness = _completeness_flags(comparison_summary)
    lines.append("## 핵심 요약" if is_ko else "## Executive Snapshot")
    lines.append(
        (
            f"- 백본={int(overall.get('backbone_count') or 0)}"
            f", SoluProt 점수 대상={int(overall.get('soluprot_total') or 0)}"
        )
        if is_ko
        else (
            f"- Backbones={int(overall.get('backbone_count') or 0)}, "
            f"SoluProt-scored designs={int(overall.get('soluprot_total') or 0)}"
        )
    )
    lines.append(
        (
            f"- SoluProt 통과={int(overall.get('soluprot_passed') or 0)}/"
            f"{int(overall.get('soluprot_total') or 0)} ({_format_ratio(overall.get('soluprot_pass_rate'))})"
        )
        if is_ko
        else (
            f"- SoluProt pass={int(overall.get('soluprot_passed') or 0)}/"
            f"{int(overall.get('soluprot_total') or 0)} ({_format_ratio(overall.get('soluprot_pass_rate'))})"
        )
    )
    lines.append(
        (
            f"- ColabFold 선발={int(overall.get('af2_selected_total') or 0)}/"
            f"{int(overall.get('af2_candidate_total') or 0)} ({_format_ratio(overall.get('af2_pass_rate'))})"
        )
        if is_ko
        else (
            f"- ColabFold selected={int(overall.get('af2_selected_total') or 0)}/"
            f"{int(overall.get('af2_candidate_total') or 0)} ({_format_ratio(overall.get('af2_pass_rate'))})"
        )
    )
    sol_dist = (
        distributions.get("soluprot")
        if isinstance(distributions.get("soluprot"), dict)
        else {}
    )
    plddt_dist = (
        distributions.get("plddt")
        if isinstance(distributions.get("plddt"), dict)
        else {}
    )
    rmsd_dist = (
        distributions.get("rmsd") if isinstance(distributions.get("rmsd"), dict) else {}
    )
    lines.append(
        (
            "- 중앙값: "
            f"SoluProt={_format_metric(sol_dist.get('median'), 3)}, "
            f"pLDDT={_format_metric(plddt_dist.get('median'), 1)}, "
            f"RMSD={_format_metric(rmsd_dist.get('median'), 2)}"
        )
        if is_ko
        else (
            "- Medians: "
            f"SoluProt={_format_metric(sol_dist.get('median'), 3)}, "
            f"pLDDT={_format_metric(plddt_dist.get('median'), 1)}, "
            f"RMSD={_format_metric(rmsd_dist.get('median'), 2)}"
        )
    )
    sol_delta = (
        wt_vs_design.get("soluprot", {}).get("delta_design_minus_wt")
        if isinstance(wt_vs_design.get("soluprot"), dict)
        else None
    )
    plddt_delta = (
        wt_vs_design.get("plddt", {}).get("delta_design_minus_wt")
        if isinstance(wt_vs_design.get("plddt"), dict)
        else None
    )
    rmsd_delta = (
        wt_vs_design.get("rmsd", {}).get("delta_design_minus_wt")
        if isinstance(wt_vs_design.get("rmsd"), dict)
        else None
    )
    if any(isinstance(v, (int, float)) for v in [sol_delta, plddt_delta, rmsd_delta]):
        sol_text = (
            f"{float(sol_delta):+.3f}" if isinstance(sol_delta, (int, float)) else "-"
        )
        plddt_text = (
            f"{float(plddt_delta):+.1f}"
            if isinstance(plddt_delta, (int, float))
            else "-"
        )
        rmsd_text = (
            f"{float(rmsd_delta):+.2f}" if isinstance(rmsd_delta, (int, float)) else "-"
        )
        lines.append(
            (f"- WT 대비 Δ: SoluProt={sol_text}, pLDDT={plddt_text}, RMSD={rmsd_text}")
            if is_ko
            else (
                f"- Δ vs WT: SoluProt={sol_text}, pLDDT={plddt_text}, RMSD={rmsd_text}"
            )
        )
    lines.append(
        (
            f"- 데이터 완전성: RFD3={'yes' if completeness.get('has_rfd3') else 'no'}, "
            f"BioEmu={'yes' if completeness.get('has_bioemu') else 'no'}, "
            f"WT compare={'on' if completeness.get('wt_compare_enabled') else 'off'}, "
            f"ColabFold selected={int(completeness.get('af2_selected') or 0)}"
        )
        if is_ko
        else (
            f"- Data completeness: RFD3={'yes' if completeness.get('has_rfd3') else 'no'}, "
            f"BioEmu={'yes' if completeness.get('has_bioemu') else 'no'}, "
            f"WT compare={'on' if completeness.get('wt_compare_enabled') else 'off'}, "
            f"ColabFold selected={int(completeness.get('af2_selected') or 0)}"
        )
    )
    lines.append("")


def _append_top_hit_lines(
    lines: list[str],
    *,
    run_root: Path,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
    lang: str = "en",
    top_n: int = 10,
) -> None:
    is_ko = str(lang).lower().startswith("ko")
    rows = _build_hit_list_rows(
        run_root=run_root,
        request=request,
        summary=summary,
        weights={"soluprot": 0.4, "plddt": 0.3, "rmsd": 0.2, "novelty": 0.0},
        rmsd_ref=5.0,
    )
    lines.append("## 주요 후보 (Hit List)" if is_ko else "## Top Candidate Hit List")
    if not rows:
        lines.append(
            "- 후보 점수 데이터를 계산할 수 없습니다."
            if is_ko
            else "- Candidate ranking data is not available."
        )
        lines.append("")
        return
    stats = _hit_list_stats(rows)
    lines.append(
        (
            f"- 총 {len(rows)}개 후보, 점수 중앙값={_format_metric(stats.get('score_median'), 1)}"
        )
        if is_ko
        else (
            f"- {len(rows)} candidates ranked, median score={_format_metric(stats.get('score_median'), 1)}"
        )
    )
    lines.append(
        "| 순위 | seq_id | Source | 서열 보존율 | Score | SoluProt | pLDDT | RMSD | WT 차이 (n/len · 상동성) | ColabFold selected |"
        if is_ko
        else "| Rank | seq_id | Source | Sequence conservation | Score | SoluProt | pLDDT | RMSD | WT change (n/len · identity) | ColabFold selected |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
    for row in rows[: max(1, int(top_n))]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("rank") or "-"),
                    str(row.get("seq_id") or "-"),
                    str(row.get("source") or "-"),
                    _format_conservation_tier_value(row.get("tier")),
                    _format_metric(row.get("score"), 1),
                    _format_metric(row.get("soluprot"), 3),
                    _format_metric(row.get("plddt"), 1),
                    _format_metric(row.get("rmsd"), 2),
                    _format_wt_difference(row),
                    "yes" if bool(row.get("af2_selected")) else "no",
                ]
            )
            + " |"
        )
    best = rows[0] if rows else None
    if isinstance(best, dict):
        lines.append(
            (
                f"- 최고 후보: {best.get('seq_id') or '-'} "
                f"(score={_format_metric(best.get('score'), 1)}, tier={_format_metric(best.get('tier'), 2)})"
            )
            if is_ko
            else (
                f"- Top candidate: {best.get('seq_id') or '-'} "
                f"(score={_format_metric(best.get('score'), 1)}, tier={_format_metric(best.get('tier'), 2)})"
            )
        )
    lines.append("")


def _collect_surrogate_triage_summaries(run_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    root_model_selection = run_root / "surrogate_triage" / "model_selection.json"
    root_payload = _load_json_file(root_model_selection)
    if isinstance(root_payload, dict):
        rows.append(
            {
                "tier": "pooled_tiers",
                "scope": root_payload.get("scope") or "pooled_tiers",
                "selected_policy": root_payload.get("selected_policy") or "-",
                "selection_strategy": root_payload.get("selection_strategy") or "-",
                "requested_policy": root_payload.get("requested_policy") or "-",
                "initial_samples": root_payload.get("initial_samples"),
                "top_k": root_payload.get("top_k"),
                "expected_af2_calls": root_payload.get("expected_af2_calls"),
                "candidate_count_before_triage": root_payload.get(
                    "candidate_count_before_triage"
                ),
                "candidate_count_after_budget": root_payload.get(
                    "candidate_count_after_budget"
                ),
                "comparator_models": root_payload.get("comparator_models") or [],
                "ensemble_models": root_payload.get("ensemble_models") or [],
                "graph_path": "surrogate_triage/model_comparison.svg",
            }
        )
    tiers_root = run_root / "tiers"
    if not tiers_root.exists():
        return rows
    for model_selection_path in sorted(
        tiers_root.glob("*/surrogate_triage/model_selection.json")
    ):
        payload = _load_json_file(model_selection_path)
        if not isinstance(payload, dict):
            continue
        tier = str(payload.get("tier") or model_selection_path.parent.parent.name)
        rows.append(
            {
                "tier": tier,
                "selected_policy": payload.get("selected_policy") or "-",
                "selection_strategy": payload.get("selection_strategy") or "-",
                "requested_policy": payload.get("requested_policy") or "-",
                "initial_samples": payload.get("initial_samples"),
                "top_k": payload.get("top_k"),
                "expected_af2_calls": payload.get("expected_af2_calls"),
                "candidate_count_before_triage": payload.get(
                    "candidate_count_before_triage"
                ),
                "candidate_count_after_budget": payload.get(
                    "candidate_count_after_budget"
                ),
                "comparator_models": payload.get("comparator_models") or [],
                "ensemble_models": payload.get("ensemble_models") or [],
                "graph_path": f"tiers/{tier}/surrogate_triage/model_comparison.svg",
            }
        )
    return rows


def _append_surrogate_triage_report_lines(
    lines: list[str], *, run_root: Path, lang: str = "en"
) -> None:
    summaries = _collect_surrogate_triage_summaries(run_root)
    if not summaries:
        return
    is_ko = str(lang).lower().startswith("ko")
    lines.append("## 대리모델 선별" if is_ko else "## Surrogate Triage")
    for item in summaries:
        if str(item.get("scope") or "") == "pooled_tiers":
            tier_label = "통합 tier" if is_ko else "Pooled tiers"
        else:
            tier_label = _format_conservation_tier_label(
                item.get("tier"), lang="ko" if is_ko else "en"
            )
        comparators = ", ".join(str(x) for x in item.get("comparator_models") or [])
        ensemble = ", ".join(str(x) for x in item.get("ensemble_models") or [])
        if is_ko:
            lines.append(
                "- "
                f"{tier_label}: 선택 정책={item.get('selected_policy')}; "
                f"선택 방식={item.get('selection_strategy')}; "
                f"AF2 호출={item.get('expected_af2_calls')} "
                f"(학습 {item.get('initial_samples')} + Top K {item.get('top_k')}); "
                f"비교 모델={comparators or '-'}; ensemble 멤버={ensemble or '-'}."
            )
        else:
            lines.append(
                "- "
                f"{tier_label}: selected policy={item.get('selected_policy')}; "
                f"selection={item.get('selection_strategy')}; "
                f"AF2 calls={item.get('expected_af2_calls')} "
                f"(training {item.get('initial_samples')} + Top K {item.get('top_k')}); "
                f"comparators={comparators or '-'}; ensemble members={ensemble or '-'}."
            )
        graph_path = str(item.get("graph_path") or "").strip()
        if graph_path:
            lines.append(
                f"![대리모델 비교]({graph_path})"
                if is_ko
                else f"![Surrogate model comparison]({graph_path})"
            )
    if is_ko:
        lines.append(
            "- 관련 아티팩트: `surrogate_triage/model_selection.json`, "
            "`cv_metrics.csv`, `model_comparison.svg`, `model_predictions.csv`, `feature_importance.csv`, "
            "`models/*.pkl`."
        )
    else:
        lines.append(
            "- Artifacts: `surrogate_triage/model_selection.json`, "
            "`cv_metrics.csv`, `model_comparison.svg`, `model_predictions.csv`, `feature_importance.csv`, "
            "`models/*.pkl`."
        )
    lines.append("")


def _build_report_text(
    *,
    run_id: str,
    run_root: Path,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
    status: dict[str, object] | None,
    feedback_items: list[dict[str, object]],
    experiment_items: list[dict[str, object]],
    agent_items: list[dict[str, object]],
) -> str:
    lines: list[str] = []
    lines.append(f"# Run Report: {run_id}")
    lines.append("")

    if status:
        lines.append("## Status")
        lines.append(f"- Stage: {status.get('stage') or '-'}")
        lines.append(f"- State: {status.get('state') or '-'}")
        lines.append(f"- Updated: {status.get('updated_at') or '-'}")
        lines.append("")

    if request:
        lines.append("## Inputs")
        target_pdb = bool(str(request.get("target_pdb") or "").strip())
        target_fasta = bool(str(request.get("target_fasta") or "").strip())
        lines.append(f"- target_pdb: {'yes' if target_pdb else 'no'}")
        lines.append(f"- target_fasta: {'yes' if target_fasta else 'no'}")
        if request.get("start_from"):
            lines.append(
                f"- start_from: {_display_pipeline_stage(request.get('start_from'))}"
            )
        if request.get("stop_after"):
            lines.append(
                f"- stop_after: {_display_pipeline_stage(request.get('stop_after'))}"
            )
        if request.get("design_chains"):
            lines.append(f"- design_chains: {request.get('design_chains')}")
        if request.get("rfd3_contig"):
            lines.append(f"- rfd3_contig: {request.get('rfd3_contig')}")
        if request.get("rfd3_input_pdb"):
            lines.append("- rfd3_input_pdb: provided")
        if request.get("diffdock_ligand_smiles") or request.get("diffdock_ligand_sdf"):
            lines.append("- diffdock_ligand: provided")
        if request.get("af2_model_preset"):
            lines.append(f"- af2_model_preset: {request.get('af2_model_preset')}")
        if request.get("mmseqs_target_db"):
            lines.append(f"- mmseqs_target_db: {request.get('mmseqs_target_db')}")
        if "wt_compare" in request:
            lines.append(
                f"- wt_compare: {'yes' if request.get('wt_compare') else 'no'}"
            )
        if "mask_consensus_apply" in request:
            lines.append(
                f"- mask_consensus_apply: {'yes' if request.get('mask_consensus_apply') else 'no'}"
            )
        if "ligand_mask_use_original_target" in request:
            lines.append(
                "- ligand_mask_use_original_target: "
                + ("yes" if request.get("ligand_mask_use_original_target") else "no")
            )
        lines.append("")

    hide_target = _should_hide_target_source(summary, run_root=run_root)

    if summary:
        errors = summary.get("errors")
        lines.append("## Summary")
        if isinstance(errors, list) and errors:
            lines.append("- Errors:")
            for err in errors[:5]:
                lines.append(f"  - {_compact_error_message(err)}")
            if len(errors) > 5:
                lines.append(f"  - ... (+{len(errors) - 5} more)")
        tiers = summary.get("tiers")
        if isinstance(tiers, list) and tiers:
            lines.append(f"- Conservation levels: {len(tiers)}")
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                tier_val = tier.get("tier")
                samples = tier.get("proteinmpnn_samples") or []
                passed = tier.get("passed_ids") or []
                selected = tier.get("af2_selected_ids") or []
                visible_seq_sources = _visible_sample_sources(
                    samples, hide_target=hide_target
                )
                use_visible_filter = bool(samples)
                design_count = (
                    len(visible_seq_sources) if use_visible_filter else len(samples)
                )
                passed_count = len(
                    _filtered_metric_ids(
                        passed if isinstance(passed, list) else [],
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                )
                selected_count = len(
                    _filtered_metric_ids(
                        selected if isinstance(selected, list) else [],
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                )
                lines.append(
                    f"  - {_format_conservation_tier_label(tier_val)}: designs={design_count} passed={passed_count} af2_selected={selected_count}"
                )
        if summary.get("msa_a3m_path"):
            lines.append(f"- msa_a3m_path: {summary.get('msa_a3m_path')}")
        if summary.get("conservation_path"):
            lines.append(f"- conservation_path: {summary.get('conservation_path')}")
        if summary.get("ligand_mask_path"):
            lines.append(f"- ligand_mask_path: {summary.get('ligand_mask_path')}")
        lines.append("")

    lines.extend(
        _mask_consensus_report_lines(run_root=run_root, request=request, lang="en")
    )

    wt_metrics = _load_wt_metrics(run_root)
    design_metrics = _collect_design_metrics(run_root, summary, hide_target=hide_target)
    source_metrics = _collect_source_metrics(run_root, summary, hide_target=hide_target)
    comparison_summary = _build_comparison_summary(
        run_root=run_root, request=request, summary=summary
    )
    _append_report_snapshot_lines(
        lines, comparison_summary=comparison_summary, lang="en"
    )
    _append_surrogate_triage_report_lines(lines, run_root=run_root, lang="en")
    if wt_metrics or (request and request.get("wt_compare")):
        lines.append("## WT Comparison")
        enabled = bool(request.get("wt_compare")) if request else False
        lines.append(f"- Enabled: {'yes' if enabled else 'no'}")

        wt_sol = wt_metrics.get("soluprot") if isinstance(wt_metrics, dict) else None
        wt_af2 = wt_metrics.get("af2") if isinstance(wt_metrics, dict) else None
        wt_sol_score: float | None = None
        design_sol_median: float | None = None
        wt_plddt_val: float | None = None
        design_plddt_median: float | None = None
        wt_rmsd_val: float | None = None
        design_rmsd_median: float | None = None

        if isinstance(wt_sol, dict) and not wt_sol.get("skipped"):
            score = wt_sol.get("score")
            cutoff = wt_sol.get("cutoff")
            passed = wt_sol.get("passed")
            if isinstance(score, (int, float)):
                score_text = f"{float(score):.3f}"
                wt_sol_score = float(score)
            else:
                score_text = "-"
            lines.append(
                f"- WT SoluProt: score={score_text} cutoff={cutoff} passed={'yes' if passed else 'no'}"
            )
        elif isinstance(wt_sol, dict):
            reason = wt_sol.get("reason") or wt_sol.get("error") or "skipped"
            lines.append(f"- WT SoluProt: skipped ({reason})")

        sol_scores = design_metrics.get("soluprot_scores") or []
        sol_total = int(design_metrics.get("soluprot_total") or 0)
        sol_passed = int(design_metrics.get("soluprot_passed") or 0)
        if sol_scores and sol_total:
            sol_median = _median(
                [float(x) for x in sol_scores if isinstance(x, (int, float))]
            )
            if sol_median is not None:
                design_sol_median = float(sol_median)
            pass_rate = (sol_passed / sol_total) if sol_total else 0.0
            lines.append(
                f"- Designs SoluProt: median={sol_median:.3f} pass_rate={pass_rate:.1%} ({sol_passed}/{sol_total})"
            )
            if isinstance(wt_sol, dict) and isinstance(
                wt_sol.get("score"), (int, float)
            ):
                delta = float(sol_median) - float(wt_sol.get("score"))
                lines.append(f"- ΔSoluProt (median - WT): {delta:+.3f}")
        elif sol_total == 0:
            lines.append("- Designs SoluProt: not available")

        if isinstance(wt_af2, dict) and not wt_af2.get("skipped"):
            wt_plddt = wt_af2.get("best_plddt")
            wt_rmsd = wt_af2.get("rmsd_ca")
            if isinstance(wt_plddt, (int, float)):
                wt_plddt_val = float(wt_plddt)
            if isinstance(wt_rmsd, (int, float)):
                wt_rmsd_val = float(wt_rmsd)
            plddt_text = (
                f"{float(wt_plddt):.1f}" if isinstance(wt_plddt, (int, float)) else "-"
            )
            rmsd_text = (
                f"{float(wt_rmsd):.2f}" if isinstance(wt_rmsd, (int, float)) else "-"
            )
            lines.append(f"- WT ColabFold: pLDDT={plddt_text} RMSD={rmsd_text}")
        elif isinstance(wt_af2, dict):
            reason = wt_af2.get("reason") or wt_af2.get("error") or "skipped"
            lines.append(f"- WT ColabFold: skipped ({reason})")

        plddt_vals = design_metrics.get("af2_plddt") or []
        rmsd_vals = _design_rmsd_values_for_wt_compare(design_metrics)
        af2_total = int(design_metrics.get("af2_candidate_total") or 0)
        if plddt_vals:
            plddt_median = _median(
                [float(x) for x in plddt_vals if isinstance(x, (int, float))]
            )
            if plddt_median is not None:
                design_plddt_median = float(plddt_median)
            plddt_max = max(plddt_vals) if plddt_vals else None
            lines.append(
                f"- Designs ColabFold pLDDT: median={plddt_median:.1f} max={float(plddt_max):.1f} (n={af2_total})"
            )
            if isinstance(wt_af2, dict) and isinstance(
                wt_af2.get("best_plddt"), (int, float)
            ):
                delta = float(plddt_median) - float(wt_af2.get("best_plddt"))
                lines.append(f"- ΔpLDDT (median - WT): {delta:+.1f}")
        else:
            lines.append("- Designs ColabFold pLDDT: not available")

        if rmsd_vals:
            rmsd_median = _median(
                [float(x) for x in rmsd_vals if isinstance(x, (int, float))]
            )
            if rmsd_median is not None:
                design_rmsd_median = float(rmsd_median)
            rmsd_min = min(rmsd_vals) if rmsd_vals else None
            lines.append(
                f"- Designs RMSD: median={rmsd_median:.2f} min={float(rmsd_min):.2f} (lower is better)"
            )
            if isinstance(wt_af2, dict) and isinstance(
                wt_af2.get("rmsd_ca"), (int, float)
            ):
                delta = float(rmsd_median) - float(wt_af2.get("rmsd_ca"))
                lines.append(f"- ΔRMSD (median - WT): {delta:+.2f} (lower is better)")
        else:
            lines.append("- Designs RMSD: not available")
        _append_wt_visual_lines(
            lines,
            wt_sol_score=wt_sol_score,
            design_sol_median=design_sol_median,
            wt_plddt=wt_plddt_val,
            design_plddt_median=design_plddt_median,
            wt_rmsd=wt_rmsd_val,
            design_rmsd_median=design_rmsd_median,
            lang="en",
        )
        lines.append("")

    _append_source_comparison_lines(lines, source_metrics=source_metrics, lang="en")
    _append_extended_comparison_lines(
        lines, comparison_summary=comparison_summary, lang="en"
    )
    _append_top_hit_lines(
        lines, run_root=run_root, request=request, summary=summary, lang="en", top_n=10
    )

    if agent_items:
        lines.append("## Agent Panel")
        for item in agent_items[-10:]:
            stage = item.get("stage") or "-"
            consensus = (
                item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
            )
            decision = consensus.get("decision") or "-"
            confidence = consensus.get("confidence")
            error = item.get("error")
            line = f"- {stage}: decision={decision}"
            if isinstance(confidence, (int, float)):
                line += f" (confidence={confidence:.2f})"
            if error:
                line += f" · error={error}"
            lines.append(line)
            actions = consensus.get("actions") if isinstance(consensus, dict) else None
            if isinstance(actions, list) and actions:
                lines.append(f"  - actions: {'; '.join(str(a) for a in actions)}")
            interpretations = (
                consensus.get("interpretations")
                if isinstance(consensus, dict)
                else None
            )
            if isinstance(interpretations, list) and interpretations:
                lines.append(
                    f"  - interpretation: {'; '.join(str(a) for a in interpretations)}"
                )
        lines.append("")

        lines.append("## Stage Interpretations")
        latest_by_stage: dict[str, dict[str, object]] = {}
        for item in agent_items:
            stage = str(item.get("stage") or "")
            if stage:
                latest_by_stage[stage] = item
        for stage, item in latest_by_stage.items():
            lines.append(f"- {stage}")
            consensus = (
                item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
            )
            interpretations = (
                consensus.get("interpretations") if isinstance(consensus, dict) else []
            )
            if isinstance(interpretations, list) and interpretations:
                for text in interpretations:
                    lines.append(f"  - {text}")
                continue
            agents = item.get("agents") if isinstance(item.get("agents"), list) else []
            fallback: list[str] = []
            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                interp = (
                    agent.get("interpretation")
                    if isinstance(agent.get("interpretation"), list)
                    else None
                )
                if isinstance(interp, list):
                    fallback.extend([str(x) for x in interp if x])
            if fallback:
                for text in fallback:
                    lines.append(f"  - {text}")
            else:
                lines.append("  - No additional interpretation.")
        lines.append("")

    feedback_counts = _summarize_feedback(feedback_items)
    experiment_counts = _summarize_experiments(experiment_items)
    score_payload = _score_payload(feedback_counts, experiment_counts)
    score = int(score_payload.get("score") or 0)
    evidence = str(score_payload.get("evidence") or "low")
    recommendation = str(score_payload.get("recommendation") or "needs_review")
    if feedback_items:
        lines.append("## Feedback")
        lines.append(f"- Good: {feedback_counts['good']}")
        lines.append(f"- Bad: {feedback_counts['bad']}")
        for item in feedback_items[:5]:
            rating = item.get("rating") or "-"
            reasons = item.get("reasons") or []
            comment = item.get("comment") or ""
            stamp = item.get("created_at") or ""
            reason_text = (
                ", ".join(str(r) for r in reasons)
                if isinstance(reasons, list)
                else str(reasons)
            )
            line = f"- [{rating}] {reason_text}"
            if comment:
                line += f" — {comment}"
            if stamp:
                line += f" ({stamp})"
            lines.append(line)
        lines.append("")

    if experiment_items:
        lines.append("## Experiments")
        lines.append(f"- Success: {experiment_counts['success']}")
        lines.append(f"- Fail: {experiment_counts['fail']}")
        lines.append(f"- Inconclusive: {experiment_counts['inconclusive']}")
        for item in experiment_items[:5]:
            assay = item.get("assay_type") or "-"
            result = item.get("result") or "-"
            metrics = item.get("metrics") or {}
            metrics_text = ""
            if isinstance(metrics, dict) and metrics:
                metrics_text = ", ".join(f"{k}={v}" for k, v in metrics.items())
            stamp = item.get("created_at") or ""
            line = f"- [{result}] {assay}"
            if metrics_text:
                line += f" ({metrics_text})"
            if stamp:
                line += f" ({stamp})"
            lines.append(line)
        lines.append("")

    lines.append("## Score")
    lines.append(f"- Score: {score}/100")
    lines.append(f"- Evidence: {evidence}")
    lines.append(f"- Recommendation: {recommendation}")
    lines.append("")

    lines.append("## Next Actions")
    if recommendation == "promote":
        lines.append("- Prioritize for downstream validation or scale-up.")
    elif recommendation == "promising":
        lines.append("- Consider additional experiments or parameter refinements.")
    elif recommendation == "needs_review":
        lines.append(
            "- Review model outputs, constraints, and consider re-running key stages."
        )
    else:
        lines.append("- Deprioritize or revisit target/constraints before re-running.")
    lines.append("")

    if len(lines) <= 2:
        lines.append("No report data available yet.")
    return "\n".join(lines).strip() + "\n"


def _build_report_text_ko(
    *,
    run_id: str,
    run_root: Path,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
    status: dict[str, object] | None,
    feedback_items: list[dict[str, object]],
    experiment_items: list[dict[str, object]],
    agent_items: list[dict[str, object]],
) -> str:
    lines: list[str] = []
    lines.append(f"# 실행 리포트: {run_id}")
    lines.append("")

    if status:
        lines.append("## 상태")
        lines.append(f"- 단계: {status.get('stage') or '-'}")
        lines.append(f"- 상태: {status.get('state') or '-'}")
        lines.append(f"- 업데이트: {status.get('updated_at') or '-'}")
        lines.append("")

    if request:
        lines.append("## 입력")
        target_pdb = bool(str(request.get("target_pdb") or "").strip())
        target_fasta = bool(str(request.get("target_fasta") or "").strip())
        lines.append(f"- target_pdb: {'yes' if target_pdb else 'no'}")
        lines.append(f"- target_fasta: {'yes' if target_fasta else 'no'}")
        if request.get("start_from"):
            lines.append(
                f"- start_from: {_display_pipeline_stage(request.get('start_from'))}"
            )
        if request.get("stop_after"):
            lines.append(
                f"- stop_after: {_display_pipeline_stage(request.get('stop_after'))}"
            )
        if request.get("design_chains"):
            lines.append(f"- design_chains: {request.get('design_chains')}")
        if request.get("rfd3_contig"):
            lines.append(f"- rfd3_contig: {request.get('rfd3_contig')}")
        if request.get("rfd3_input_pdb"):
            lines.append("- rfd3_input_pdb: provided")
        if request.get("diffdock_ligand_smiles") or request.get("diffdock_ligand_sdf"):
            lines.append("- diffdock_ligand: provided")
        if request.get("af2_model_preset"):
            lines.append(f"- af2_model_preset: {request.get('af2_model_preset')}")
        if request.get("mmseqs_target_db"):
            lines.append(f"- mmseqs_target_db: {request.get('mmseqs_target_db')}")
        if "wt_compare" in request:
            lines.append(
                f"- wt_compare: {'yes' if request.get('wt_compare') else 'no'}"
            )
        if "mask_consensus_apply" in request:
            lines.append(
                f"- mask_consensus_apply: {'yes' if request.get('mask_consensus_apply') else 'no'}"
            )
        if "ligand_mask_use_original_target" in request:
            lines.append(
                "- ligand_mask_use_original_target: "
                + ("yes" if request.get("ligand_mask_use_original_target") else "no")
            )
        lines.append("")

    hide_target = _should_hide_target_source(summary, run_root=run_root)

    if summary:
        errors = summary.get("errors")
        lines.append("## 요약")
        if isinstance(errors, list) and errors:
            lines.append("- 오류:")
            for err in errors[:5]:
                lines.append(f"  - {_compact_error_message(err)}")
            if len(errors) > 5:
                lines.append(f"  - ... (+{len(errors) - 5} more)")
        tiers = summary.get("tiers")
        if isinstance(tiers, list) and tiers:
            lines.append(f"- 서열 보존율 구간 수: {len(tiers)}")
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                tier_val = tier.get("tier")
                samples = tier.get("proteinmpnn_samples") or []
                passed = tier.get("passed_ids") or []
                selected = tier.get("af2_selected_ids") or []
                visible_seq_sources = _visible_sample_sources(
                    samples, hide_target=hide_target
                )
                use_visible_filter = bool(samples)
                design_count = (
                    len(visible_seq_sources) if use_visible_filter else len(samples)
                )
                passed_count = len(
                    _filtered_metric_ids(
                        passed if isinstance(passed, list) else [],
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                )
                selected_count = len(
                    _filtered_metric_ids(
                        selected if isinstance(selected, list) else [],
                        visible_seq_sources,
                        use_visible_filter=use_visible_filter,
                    )
                )
                lines.append(
                    f"  - {_format_conservation_tier_label(tier_val, lang='ko')}: designs={design_count} passed={passed_count} af2_selected={selected_count}"
                )
        if summary.get("msa_a3m_path"):
            lines.append(f"- msa_a3m_path: {summary.get('msa_a3m_path')}")
        if summary.get("conservation_path"):
            lines.append(f"- conservation_path: {summary.get('conservation_path')}")
        if summary.get("ligand_mask_path"):
            lines.append(f"- ligand_mask_path: {summary.get('ligand_mask_path')}")
        lines.append("")

    lines.extend(
        _mask_consensus_report_lines(run_root=run_root, request=request, lang="ko")
    )

    wt_metrics = _load_wt_metrics(run_root)
    design_metrics = _collect_design_metrics(run_root, summary, hide_target=hide_target)
    source_metrics = _collect_source_metrics(run_root, summary, hide_target=hide_target)
    comparison_summary = _build_comparison_summary(
        run_root=run_root, request=request, summary=summary
    )
    _append_report_snapshot_lines(
        lines, comparison_summary=comparison_summary, lang="ko"
    )
    _append_surrogate_triage_report_lines(lines, run_root=run_root, lang="ko")
    if wt_metrics or (request and request.get("wt_compare")):
        lines.append("## WT 비교")
        enabled = bool(request.get("wt_compare")) if request else False
        lines.append(f"- 사용 여부: {'yes' if enabled else 'no'}")

        wt_sol = wt_metrics.get("soluprot") if isinstance(wt_metrics, dict) else None
        wt_af2 = wt_metrics.get("af2") if isinstance(wt_metrics, dict) else None
        wt_sol_score: float | None = None
        design_sol_median: float | None = None
        wt_plddt_val: float | None = None
        design_plddt_median: float | None = None
        wt_rmsd_val: float | None = None
        design_rmsd_median: float | None = None

        if isinstance(wt_sol, dict) and not wt_sol.get("skipped"):
            score = wt_sol.get("score")
            cutoff = wt_sol.get("cutoff")
            passed = wt_sol.get("passed")
            if isinstance(score, (int, float)):
                score_text = f"{float(score):.3f}"
                wt_sol_score = float(score)
            else:
                score_text = "-"
            lines.append(
                f"- WT SoluProt: score={score_text} cutoff={cutoff} passed={'yes' if passed else 'no'}"
            )
        elif isinstance(wt_sol, dict):
            reason = wt_sol.get("reason") or wt_sol.get("error") or "skipped"
            lines.append(f"- WT SoluProt: skipped ({reason})")

        sol_scores = design_metrics.get("soluprot_scores") or []
        sol_total = int(design_metrics.get("soluprot_total") or 0)
        sol_passed = int(design_metrics.get("soluprot_passed") or 0)
        if sol_scores and sol_total:
            sol_median = _median(
                [float(x) for x in sol_scores if isinstance(x, (int, float))]
            )
            if sol_median is not None:
                design_sol_median = float(sol_median)
            pass_rate = (sol_passed / sol_total) if sol_total else 0.0
            lines.append(
                f"- Designs SoluProt: median={sol_median:.3f} pass_rate={pass_rate:.1%} ({sol_passed}/{sol_total})"
            )
            if isinstance(wt_sol, dict) and isinstance(
                wt_sol.get("score"), (int, float)
            ):
                delta = float(sol_median) - float(wt_sol.get("score"))
                lines.append(f"- ΔSoluProt (median - WT): {delta:+.3f}")
        elif sol_total == 0:
            lines.append("- Designs SoluProt: not available")

        if isinstance(wt_af2, dict) and not wt_af2.get("skipped"):
            wt_plddt = wt_af2.get("best_plddt")
            wt_rmsd = wt_af2.get("rmsd_ca")
            if isinstance(wt_plddt, (int, float)):
                wt_plddt_val = float(wt_plddt)
            if isinstance(wt_rmsd, (int, float)):
                wt_rmsd_val = float(wt_rmsd)
            plddt_text = (
                f"{float(wt_plddt):.1f}" if isinstance(wt_plddt, (int, float)) else "-"
            )
            rmsd_text = (
                f"{float(wt_rmsd):.2f}" if isinstance(wt_rmsd, (int, float)) else "-"
            )
            lines.append(f"- WT ColabFold: pLDDT={plddt_text} RMSD={rmsd_text}")
        elif isinstance(wt_af2, dict):
            reason = wt_af2.get("reason") or wt_af2.get("error") or "skipped"
            lines.append(f"- WT ColabFold: skipped ({reason})")

        plddt_vals = design_metrics.get("af2_plddt") or []
        rmsd_vals = _design_rmsd_values_for_wt_compare(design_metrics)
        af2_total = int(design_metrics.get("af2_candidate_total") or 0)
        if plddt_vals:
            plddt_median = _median(
                [float(x) for x in plddt_vals if isinstance(x, (int, float))]
            )
            if plddt_median is not None:
                design_plddt_median = float(plddt_median)
            plddt_max = max(plddt_vals) if plddt_vals else None
            lines.append(
                f"- Designs ColabFold pLDDT: median={plddt_median:.1f} max={float(plddt_max):.1f} (n={af2_total})"
            )
            if isinstance(wt_af2, dict) and isinstance(
                wt_af2.get("best_plddt"), (int, float)
            ):
                delta = float(plddt_median) - float(wt_af2.get("best_plddt"))
                lines.append(f"- ΔpLDDT (median - WT): {delta:+.1f}")
        else:
            lines.append("- Designs ColabFold pLDDT: not available")

        if rmsd_vals:
            rmsd_median = _median(
                [float(x) for x in rmsd_vals if isinstance(x, (int, float))]
            )
            if rmsd_median is not None:
                design_rmsd_median = float(rmsd_median)
            rmsd_min = min(rmsd_vals) if rmsd_vals else None
            lines.append(
                f"- Designs RMSD: median={rmsd_median:.2f} min={float(rmsd_min):.2f} (lower is better)"
            )
            if isinstance(wt_af2, dict) and isinstance(
                wt_af2.get("rmsd_ca"), (int, float)
            ):
                delta = float(rmsd_median) - float(wt_af2.get("rmsd_ca"))
                lines.append(f"- ΔRMSD (median - WT): {delta:+.2f} (lower is better)")
        else:
            lines.append("- Designs RMSD: not available")
        _append_wt_visual_lines(
            lines,
            wt_sol_score=wt_sol_score,
            design_sol_median=design_sol_median,
            wt_plddt=wt_plddt_val,
            design_plddt_median=design_plddt_median,
            wt_rmsd=wt_rmsd_val,
            design_rmsd_median=design_rmsd_median,
            lang="ko",
        )
        lines.append("")

    _append_source_comparison_lines(lines, source_metrics=source_metrics, lang="ko")
    _append_extended_comparison_lines(
        lines, comparison_summary=comparison_summary, lang="ko"
    )
    _append_top_hit_lines(
        lines, run_root=run_root, request=request, summary=summary, lang="ko", top_n=10
    )

    if agent_items:
        lines.append("## 에이전트 패널")
        for item in agent_items[-10:]:
            stage = item.get("stage") or "-"
            consensus = (
                item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
            )
            decision = consensus.get("decision") or "-"
            confidence = consensus.get("confidence")
            error = item.get("error")
            line = f"- {stage}: 결정={decision}"
            if isinstance(confidence, (int, float)):
                line += f" (confidence={confidence:.2f})"
            if error:
                line += f" · error={error}"
            lines.append(line)
            actions = consensus.get("actions") if isinstance(consensus, dict) else None
            if isinstance(actions, list) and actions:
                lines.append(f"  - actions: {'; '.join(str(a) for a in actions)}")
            interpretations = (
                consensus.get("interpretations")
                if isinstance(consensus, dict)
                else None
            )
            if isinstance(interpretations, list) and interpretations:
                lines.append(
                    f"  - interpretation: {'; '.join(str(a) for a in interpretations)}"
                )
        lines.append("")

        lines.append("## 단계 해석")
        latest_by_stage: dict[str, dict[str, object]] = {}
        for item in agent_items:
            stage = str(item.get("stage") or "")
            if stage:
                latest_by_stage[stage] = item
        for stage, item in latest_by_stage.items():
            lines.append(f"- {stage}")
            consensus = (
                item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
            )
            interpretations = (
                consensus.get("interpretations")
                if isinstance(consensus.get("interpretations"), list)
                else []
            )
            if isinstance(interpretations, list) and interpretations:
                for text in interpretations:
                    lines.append(f"  - {text}")
                continue
            agents = item.get("agents") if isinstance(item.get("agents"), list) else []
            fallback: list[str] = []
            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                interp = (
                    agent.get("interpretation")
                    if isinstance(agent.get("interpretation"), list)
                    else None
                )
                if isinstance(interp, list):
                    fallback.extend([str(x) for x in interp if x])
            if fallback:
                for text in fallback:
                    lines.append(f"  - {text}")
            else:
                lines.append("  - 추가 해석 없음.")
        lines.append("")

    feedback_counts = _summarize_feedback(feedback_items)
    experiment_counts = _summarize_experiments(experiment_items)
    score_payload = _score_payload(feedback_counts, experiment_counts)
    score = int(score_payload.get("score") or 0)
    evidence = str(score_payload.get("evidence") or "low")
    recommendation = str(score_payload.get("recommendation") or "needs_review")
    if feedback_items:
        lines.append("## 피드백")
        lines.append(f"- Good: {feedback_counts['good']}")
        lines.append(f"- Bad: {feedback_counts['bad']}")
        for item in feedback_items[:5]:
            rating = item.get("rating") or "-"
            reasons = item.get("reasons") or []
            comment = item.get("comment") or ""
            stamp = item.get("created_at") or ""
            reason_text = (
                ", ".join(str(r) for r in reasons)
                if isinstance(reasons, list)
                else str(reasons)
            )
            line = f"- [{rating}] {reason_text}"
            if comment:
                line += f" — {comment}"
            if stamp:
                line += f" ({stamp})"
            lines.append(line)
        lines.append("")

    if experiment_items:
        lines.append("## 실험")
        lines.append(f"- Success: {experiment_counts['success']}")
        lines.append(f"- Fail: {experiment_counts['fail']}")
        lines.append(f"- Inconclusive: {experiment_counts['inconclusive']}")
        for item in experiment_items[:5]:
            assay = item.get("assay_type") or "-"
            result = item.get("result") or "-"
            metrics = item.get("metrics") or {}
            metrics_text = ""
            if isinstance(metrics, dict) and metrics:
                metrics_text = ", ".join(f"{k}={v}" for k, v in metrics.items())
            stamp = item.get("created_at") or ""
            line = f"- [{result}] {assay}"
            if metrics_text:
                line += f" ({metrics_text})"
            if stamp:
                line += f" ({stamp})"
            lines.append(line)
        lines.append("")

    lines.append("## 점수")
    lines.append(f"- Score: {score}/100")
    lines.append(f"- Evidence: {evidence}")
    lines.append(f"- Recommendation: {recommendation}")
    lines.append("")

    lines.append("## 다음 권장 조치")
    if recommendation == "promote":
        lines.append("- 후속 검증 또는 스케일업을 우선 진행하세요.")
    elif recommendation == "promising":
        lines.append("- 추가 실험 또는 파라미터 조정을 고려하세요.")
    elif recommendation == "needs_review":
        lines.append("- 결과, 제약, 주요 단계 재실행 여부를 검토하세요.")
    else:
        lines.append("- 타깃/제약을 재검토한 뒤 재실행을 권장합니다.")
    lines.append("")

    if len(lines) <= 2:
        lines.append("리포트 데이터가 아직 없습니다.")
    return "\n".join(lines).strip() + "\n"


def _generate_report(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")

    root = resolve_run_path(runner.output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")
    summary = None
    summary_path = root / "summary.json"
    if summary_path.exists():
        raw = read_json(summary_path)
        if isinstance(raw, dict):
            summary = raw
    request = None
    request_path = root / "request.json"
    if request_path.exists():
        raw = read_json(request_path)
        if isinstance(raw, dict):
            request = raw

    status = load_status(runner.output_root, run_id)
    feedback_items = list_run_events(
        runner.output_root, run_id, filename="feedback.jsonl", limit=50
    )
    experiment_items = list_run_events(
        runner.output_root, run_id, filename="experiments.jsonl", limit=50
    )
    agent_items = list_run_events(
        runner.output_root, run_id, filename="agent_panel.jsonl", limit=50
    )
    feedback_counts = _summarize_feedback(feedback_items)
    experiment_counts = _summarize_experiments(experiment_items)
    score_payload = _score_payload(feedback_counts, experiment_counts)
    score = int(score_payload.get("score") or 0)
    evidence = str(score_payload.get("evidence") or "low")
    recommendation = str(score_payload.get("recommendation") or "needs_review")
    report_text = _build_report_text(
        run_id=run_id,
        run_root=root,
        request=request,
        summary=summary,
        status=status,
        feedback_items=feedback_items,
        experiment_items=experiment_items,
        agent_items=agent_items,
    )
    report_text_ko = _build_report_text_ko(
        run_id=run_id,
        run_root=root,
        request=request,
        summary=summary,
        status=status,
        feedback_items=feedback_items,
        experiment_items=experiment_items,
        agent_items=agent_items,
    )
    comparison_summary = _build_comparison_summary(
        run_root=root, request=request, summary=summary
    )
    write_json(root / "comparisons.json", comparison_summary)
    _save_report_text(runner.output_root, run_id, report_text)
    _save_report_text_ko(runner.output_root, run_id, report_text_ko)
    entry: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "source": "generated",
        "content": report_text,
        "content_ko": report_text_ko,
        "score": score,
        "evidence": evidence,
        "recommendation": recommendation,
        "scoring_config": score_payload.get("scoring_config") or scoring_config(),
        "created_at": _now_iso(),
    }
    append_run_event(
        runner.output_root, run_id, filename="report_revisions.jsonl", payload=entry
    )
    return {
        "run_id": run_id,
        "report": report_text,
        "report_ko": report_text_ko,
        "comparison_summary": comparison_summary,
        "score": score,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def _save_report(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    content = _as_text(arguments.get("content")).strip()
    if not content:
        raise ValueError("content is required")

    user = _normalize_user(arguments.get("user"))
    source = str(arguments.get("source") or "user").strip()
    _save_report_text(runner.output_root, run_id, content)
    saved_attachments = _save_report_attachments(
        runner.output_root, run_id, arguments.get("attachments")
    )
    entry: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "source": source,
        "content": content,
        "user": user,
        "created_at": _now_iso(),
    }
    if saved_attachments:
        entry["attachments"] = saved_attachments
    append_run_event(
        runner.output_root, run_id, filename="report_revisions.jsonl", payload=entry
    )
    out: dict[str, object] = {"run_id": run_id, "report": content}
    if saved_attachments:
        out["attachments"] = saved_attachments
    return out


def _get_report(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    report_text = _load_report_text(runner.output_root, run_id)
    root = resolve_run_path(runner.output_root, run_id)
    report_ko = ""
    report_ko_path = root / "report_ko.md"
    if report_ko_path.exists():
        try:
            report_ko = report_ko_path.read_text(encoding="utf-8")
        except Exception:
            report_ko = ""
    revisions = list_run_events(
        runner.output_root, run_id, filename="report_revisions.jsonl", limit=1
    )
    latest = revisions[-1] if revisions else None
    out = {
        "run_id": run_id,
        "report": report_text or "",
        "report_ko": report_ko or "",
        "latest_revision": latest,
    }
    if isinstance(latest, dict):
        for key in ("score", "evidence", "recommendation"):
            if key in latest:
                out[key] = latest.get(key)
    if "score" not in out:
        feedback_items = list_run_events(
            runner.output_root, run_id, filename="feedback.jsonl", limit=50
        )
        experiment_items = list_run_events(
            runner.output_root, run_id, filename="experiments.jsonl", limit=50
        )
        feedback_counts = _summarize_feedback(feedback_items)
        experiment_counts = _summarize_experiments(experiment_items)
        score_payload = _score_payload(feedback_counts, experiment_counts)
        out["score"] = score_payload.get("score")
        out["evidence"] = score_payload.get("evidence")
        out["recommendation"] = score_payload.get("recommendation")

    request_payload = None
    request_path = root / "request.json"
    if request_path.exists():
        raw = read_json(request_path)
        if isinstance(raw, dict):
            request_payload = raw
    summary_payload = None
    summary_path = root / "summary.json"
    if summary_path.exists():
        raw = read_json(summary_path)
        if isinstance(raw, dict):
            summary_payload = raw
    out["comparison_summary"] = _build_comparison_summary(
        run_root=root,
        request=request_payload,
        summary=summary_payload,
    )
    return out


def _load_run_request_summary(
    run_root: Path,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    request = _load_json_file(run_root / "request.json")
    summary = _load_json_file(run_root / "summary.json")
    return request, summary


def _to_float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _comparison_snapshot(
    comparison: dict[str, object] | None,
) -> dict[str, float | None]:
    if not isinstance(comparison, dict):
        return {
            "soluprot_median": None,
            "plddt_median": None,
            "rmsd_median": None,
            "relax_median": None,
            "soluprot_pass_rate": None,
            "af2_pass_rate": None,
            "backbone_count": None,
        }
    wt = (
        comparison.get("wt_vs_design")
        if isinstance(comparison.get("wt_vs_design"), dict)
        else {}
    )
    funnel = (
        comparison.get("funnel") if isinstance(comparison.get("funnel"), dict) else {}
    )
    overall = funnel.get("overall") if isinstance(funnel.get("overall"), dict) else {}
    sol = wt.get("soluprot") if isinstance(wt.get("soluprot"), dict) else {}
    plddt = wt.get("plddt") if isinstance(wt.get("plddt"), dict) else {}
    rmsd = wt.get("rmsd") if isinstance(wt.get("rmsd"), dict) else {}
    relax = wt.get("relax") if isinstance(wt.get("relax"), dict) else {}
    return {
        "soluprot_median": _to_float_or_none(sol.get("design_median")),
        "plddt_median": _to_float_or_none(plddt.get("design_median")),
        "rmsd_median": _to_float_or_none(rmsd.get("design_median")),
        "relax_median": _to_float_or_none(relax.get("design_median")),
        "soluprot_pass_rate": _to_float_or_none(overall.get("soluprot_pass_rate")),
        "af2_pass_rate": _to_float_or_none(overall.get("af2_pass_rate")),
        "backbone_count": _to_float_or_none(overall.get("backbone_count")),
    }


def _compute_comparison_delta(
    current: dict[str, float | None], baseline: dict[str, float | None]
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, value in current.items():
        base = baseline.get(key)
        if value is None or base is None:
            out[key] = None
            continue
        out[key] = float(value - base)
    return out


def _completeness_flags(comparison: dict[str, object] | None) -> dict[str, object]:
    source = (
        comparison.get("source_compare")
        if isinstance(comparison, dict)
        and isinstance(comparison.get("source_compare"), dict)
        else {}
    )
    funnel = (
        comparison.get("funnel")
        if isinstance(comparison, dict) and isinstance(comparison.get("funnel"), dict)
        else {}
    )
    overall = funnel.get("overall") if isinstance(funnel.get("overall"), dict) else {}
    rfd3 = source.get("rfd3") if isinstance(source.get("rfd3"), dict) else {}
    bioemu = source.get("bioemu") if isinstance(source.get("bioemu"), dict) else {}
    has_rfd3 = int(rfd3.get("backbone_count") or 0) > 0
    has_bioemu = int(bioemu.get("backbone_count") or 0) > 0
    return {
        "has_rfd3": has_rfd3,
        "has_bioemu": has_bioemu,
        "bioemu_only": has_bioemu and not has_rfd3,
        "rfd3_missing": not has_rfd3,
        "bioemu_missing": not has_bioemu,
        "wt_compare_enabled": bool(comparison.get("wt_compare_enabled"))
        if isinstance(comparison, dict)
        else False,
        "af2_candidates": int(overall.get("af2_candidate_total") or 0),
        "af2_selected": int(overall.get("af2_selected_total") or 0),
    }


def _pick_baseline_run_id(output_root: str, run_id: str) -> str | None:
    runs = list_runs(output_root, limit=200)
    for item in runs:
        rid = str(item or "").strip()
        if not rid or rid == run_id:
            continue
        return rid
    return None


def _compare_runs(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    baseline_run_id = str(arguments.get("baseline_run_id") or "").strip() or None
    if baseline_run_id is None:
        baseline_run_id = _pick_baseline_run_id(runner.output_root, run_id)
    if not baseline_run_id:
        raise ValueError("baseline_run_id is required when no prior run exists")

    current_root = resolve_run_path(runner.output_root, run_id)
    baseline_root = resolve_run_path(runner.output_root, baseline_run_id)
    if not current_root.exists():
        raise ValueError("run_id not found")
    if not baseline_root.exists():
        raise ValueError("baseline_run_id not found")

    current_request, current_summary = _load_run_request_summary(current_root)
    baseline_request, baseline_summary = _load_run_request_summary(baseline_root)
    current_comparison = _build_comparison_summary(
        run_root=current_root,
        request=current_request,
        summary=current_summary,
    )
    baseline_comparison = _build_comparison_summary(
        run_root=baseline_root,
        request=baseline_request,
        summary=baseline_summary,
    )
    current_metrics = _comparison_snapshot(current_comparison)
    baseline_metrics = _comparison_snapshot(baseline_comparison)
    delta_metrics = _compute_comparison_delta(current_metrics, baseline_metrics)
    return {
        "run_id": run_id,
        "baseline_run_id": baseline_run_id,
        "current": current_metrics,
        "baseline": baseline_metrics,
        "delta": delta_metrics,
        "completeness": {
            "current": _completeness_flags(current_comparison),
            "baseline": _completeness_flags(baseline_comparison),
        },
    }


def _normalize_hit_weights(raw: object | None) -> dict[str, float]:
    defaults = {"soluprot": 0.4, "plddt": 0.3, "rmsd": 0.2, "novelty": 0.0}
    if not isinstance(raw, dict):
        return defaults
    out: dict[str, float] = {}
    for key in defaults:
        value = raw.get(key)
        if isinstance(value, (int, float)):
            out[key] = max(0.0, float(value))
        else:
            out[key] = defaults[key]
    if sum(out.values()) <= 0.0:
        return defaults
    scored_sum = out.get("soluprot", 0.0) + out.get("plddt", 0.0) + out.get("rmsd", 0.0)
    if scored_sum <= 0.0:
        out["soluprot"] = defaults["soluprot"]
        out["plddt"] = defaults["plddt"]
        out["rmsd"] = defaults["rmsd"]
    return out


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _build_hit_list_rows(
    *,
    run_root: Path,
    request: dict[str, object] | None,
    summary: dict[str, object] | None,
    weights: dict[str, float],
    rmsd_ref: float,
) -> list[dict[str, object]]:
    if not isinstance(summary, dict):
        return []
    tiers = summary.get("tiers")
    if not isinstance(tiers, list):
        return []
    hide_target = _should_hide_target_source(summary, run_root=run_root)
    target_sequence = _extract_primary_target_sequence(request, run_root=run_root)
    scored_weight_keys = ("soluprot", "plddt", "rmsd")
    total_weight = float(
        sum(max(0.0, float(weights.get(key, 0.0))) for key in scored_weight_keys)
    )
    rows: list[dict[str, object]] = []

    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        raw_tier = tier.get("tier")
        if raw_tier is None:
            continue
        try:
            tier_num = float(raw_tier)
        except Exception:
            continue
        tier_key = _tier_key(tier_num)
        tier_dir = run_root / "tiers" / tier_key
        samples = (
            tier.get("proteinmpnn_samples")
            if isinstance(tier.get("proteinmpnn_samples"), list)
            else []
        )
        visible_seq_sources = _visible_sample_sources(samples, hide_target=hide_target)
        use_visible_filter = bool(samples)

        sol_scores: dict[str, float] = {}
        passed_ids: set[str] = set()
        sol = _load_json_file(tier_dir / "soluprot.json")
        if isinstance(sol, dict):
            raw_scores = sol.get("scores")
            if isinstance(raw_scores, dict):
                for seq_id, raw_score in raw_scores.items():
                    if isinstance(raw_score, (int, float)):
                        sol_scores[str(seq_id)] = float(raw_score)
            raw_passed = sol.get("passed_ids")
            if isinstance(raw_passed, list):
                passed_ids = {str(x) for x in raw_passed if str(x).strip()}

        af2_scores: dict[str, float] = {}
        af2_rmsd: dict[str, float] = {}
        af2_target_rmsd: dict[str, float] = {}
        af2_selected: set[str] = set()
        af2_candidates: set[str] = set()
        af2 = _load_json_file(tier_dir / "af2_scores.json")
        if isinstance(af2, dict):
            recovered_failure = _af2_payload_has_recovered_failure(af2)
            raw_af2_scores = af2.get("scores")
            if isinstance(raw_af2_scores, dict) and not recovered_failure:
                for seq_id, raw_score in raw_af2_scores.items():
                    if isinstance(raw_score, (int, float)):
                        af2_scores[str(seq_id)] = float(raw_score)
            raw_rmsd = af2.get("rmsd_scores")
            if isinstance(raw_rmsd, dict) and not recovered_failure:
                for seq_id, raw_score in raw_rmsd.items():
                    if isinstance(raw_score, (int, float)):
                        af2_rmsd[str(seq_id)] = float(raw_score)
            raw_target_rmsd = af2.get("target_rmsd_scores")
            if isinstance(raw_target_rmsd, dict) and not recovered_failure:
                for seq_id, raw_score in raw_target_rmsd.items():
                    if isinstance(raw_score, (int, float)):
                        af2_target_rmsd[str(seq_id)] = float(raw_score)
            raw_selected = af2.get("selected_ids")
            if isinstance(raw_selected, list) and not recovered_failure:
                af2_selected = {str(x) for x in raw_selected if str(x).strip()}
            raw_candidates = af2.get("candidate_ids")
            if isinstance(raw_candidates, list):
                af2_candidates = {str(x) for x in raw_candidates if str(x).strip()}

        # Prefer per-candidate AF2 metrics because they explicitly track
        # parent-backbone RMSD reference mode.
        af2_rmsd_cache: dict[str, float | None] = {}

        def _hit_list_backbone_rmsd(seq_id: str) -> float | None:
            seq_key = str(seq_id or "").strip()
            if not seq_key:
                return None
            if seq_key in af2_rmsd_cache:
                return af2_rmsd_cache[seq_key]
            metrics = _load_json_file(
                tier_dir / "af2" / _safe_id(seq_key) / "metrics.json"
            )
            if isinstance(metrics, dict):
                mode = str(metrics.get("rmsd_reference_mode") or "").strip()
                raw_metrics_rmsd = metrics.get("rmsd_ca")
                if isinstance(raw_metrics_rmsd, (int, float)) and (
                    not mode or mode == "parent_backbone"
                ):
                    af2_rmsd_cache[seq_key] = float(raw_metrics_rmsd)
                    return af2_rmsd_cache[seq_key]
            af2_rmsd_cache[seq_key] = af2_rmsd.get(seq_key)
            return af2_rmsd_cache[seq_key]

        relax_scores: dict[str, float] = {}
        relax_selected: set[str] = set()
        relax = _load_json_file(tier_dir / "relax_scores.json")
        if isinstance(relax, dict):
            recovered_failure = _relax_payload_has_recovered_failure(relax)
            raw_relax_scores = relax.get("score_per_residue")
            if isinstance(raw_relax_scores, dict) and not recovered_failure:
                for seq_id, raw_score in raw_relax_scores.items():
                    if isinstance(raw_score, (int, float)):
                        relax_scores[str(seq_id)] = float(raw_score)
            raw_selected = relax.get("selected_ids")
            if isinstance(raw_selected, list) and not recovered_failure:
                relax_selected = {str(x) for x in raw_selected if str(x).strip()}

        for sample in samples:
            if not isinstance(sample, dict):
                continue
            seq_id = str(sample.get("id") or "").strip()
            if not seq_id:
                continue
            if not _should_include_seq_id(
                seq_id, visible_seq_sources, use_visible_filter=use_visible_filter
            ):
                continue
            sequence = _normalize_sequence(sample.get("sequence"))
            meta = sample.get("meta") if isinstance(sample.get("meta"), dict) else {}
            source = _visible_backbone_source(
                meta.get("backbone_source") if isinstance(meta, dict) else None,
                hide_target=hide_target,
            )
            if source is None:
                continue
            soluprot = sol_scores.get(seq_id)
            plddt = af2_scores.get(seq_id)
            rmsd = _hit_list_backbone_rmsd(seq_id)
            target_rmsd = af2_target_rmsd.get(seq_id)
            relax_score = relax_scores.get(seq_id)
            wt_compare = (
                _sequence_difference_stats(target_sequence or "", sequence)
                if target_sequence
                else None
            )
            wt_identity = (
                wt_compare.get("identity") if isinstance(wt_compare, dict) else None
            )
            wt_identity_pct = (
                wt_compare.get("identity_pct") if isinstance(wt_compare, dict) else None
            )
            wt_diff_count = (
                wt_compare.get("difference_count")
                if isinstance(wt_compare, dict)
                else None
            )
            wt_compare_len = (
                wt_compare.get("compare_length")
                if isinstance(wt_compare, dict)
                else None
            )
            wt_diff_ratio = (
                wt_compare.get("difference_ratio")
                if isinstance(wt_compare, dict)
                else None
            )
            wt_diff_pct = (
                wt_compare.get("difference_pct")
                if isinstance(wt_compare, dict)
                else None
            )
            novelty = wt_diff_ratio if isinstance(wt_diff_ratio, (int, float)) else None

            component_scores: dict[str, float] = {}
            if soluprot is not None:
                component_scores["soluprot"] = _clamp01(soluprot)
            if plddt is not None:
                component_scores["plddt"] = _clamp01(plddt / 100.0)
            if rmsd is not None:
                component_scores["rmsd"] = 1.0 - _clamp01(
                    rmsd / max(1e-6, float(rmsd_ref))
                )
            if novelty is not None:
                component_scores["novelty"] = _clamp01(novelty)

            used_weight = 0.0
            weighted_sum = 0.0
            for key in scored_weight_keys:
                weight = weights.get(key)
                score = component_scores.get(key)
                if score is None:
                    continue
                ww = max(0.0, float(weight))
                if ww <= 0.0:
                    continue
                used_weight += ww
                weighted_sum += ww * score
            score_norm = (weighted_sum / used_weight) if used_weight > 0 else None
            coverage = (used_weight / total_weight) if total_weight > 0 else 0.0
            composite_score = (
                (score_norm * 100.0 * coverage) if score_norm is not None else None
            )

            ranked_path = tier_dir / "af2" / _safe_id(seq_id) / "ranked_0.pdb"
            ranked_rel = (
                f"tiers/{tier_key}/af2/{_safe_id(seq_id)}/ranked_0.pdb"
                if ranked_path.exists()
                else None
            )
            rows.append(
                {
                    "seq_id": seq_id,
                    "tier": tier_num,
                    "source": source,
                    "sequence": sequence,
                    "soluprot": soluprot,
                    "plddt": plddt,
                    "rmsd": rmsd,
                    "rmsd_target": target_rmsd,
                    "relax": relax_score,
                    "wt_identity": wt_identity,
                    "wt_identity_pct": wt_identity_pct,
                    "wt_diff_count": wt_diff_count,
                    "wt_compare_len": wt_compare_len,
                    "wt_diff_ratio": wt_diff_ratio,
                    "wt_diff_pct": wt_diff_pct,
                    "novelty": novelty,
                    "soluprot_passed": seq_id in passed_ids,
                    "af2_candidate": seq_id in af2_candidates or seq_id in af2_scores,
                    "af2_selected": seq_id in af2_selected,
                    "relax_selected": seq_id in relax_selected,
                    "component_scores": component_scores,
                    "score_norm": score_norm,
                    "score": composite_score,
                    "coverage": coverage,
                    "af2_ranked_pdb_path": ranked_rel,
                }
            )

    rows.sort(
        key=lambda row: (
            row.get("score") is not None,
            float(row.get("score") or 0.0),
            float(row.get("plddt") or 0.0),
            -float(row.get("rmsd") or 0.0),
            float(row.get("soluprot") or 0.0),
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def _first_csv_float(row: dict[str, object], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in row:
            value = _csv_float_or_none(row.get(key))
            if value is not None:
                return value
    return None


def _first_csv_int(row: dict[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in row:
            value = _csv_int_or_none(row.get(key))
            if value is not None:
                return value
    return None


def _evolution_design_artifact_path(run_root: Path, seq_id: str) -> str | None:
    if not seq_id:
        return None
    designs_dir = run_root / "evolution" / "designs"
    for suffix in ("_relaxed.pdb", ".pdb"):
        candidate = designs_dir / f"{seq_id}{suffix}"
        if candidate.exists() and candidate.is_file():
            return f"evolution/designs/{seq_id}{suffix}"
    return None


def _build_evolution_hit_list_rows(
    *,
    run_root: Path,
    summary: dict[str, object] | None,
    weights: dict[str, float],
    rmsd_ref: float,
) -> list[dict[str, object]]:
    if not isinstance(summary, dict) or not summary.get("evolution_mode"):
        return []
    samples = summary.get("evaluated_samples")
    if not isinstance(samples, list):
        return []

    scored_weight_keys = ("soluprot", "plddt", "rmsd")
    total_weight = float(
        sum(max(0.0, float(weights.get(key, 0.0))) for key in scored_weight_keys)
    )
    rows: list[dict[str, object]] = []
    surrogate_model = str(summary.get("surrogate_model") or "").strip() or None
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        seq_id = str(sample.get("id") or sample.get("seq_id") or "").strip()
        if not seq_id:
            continue
        plddt = _first_csv_float(sample, ("plddt", "af2_label", "score"))
        soluprot = _first_csv_float(sample, ("soluprot", "soluprot_score"))
        rmsd = _first_csv_float(sample, ("rmsd", "rmsd_ca", "backbone_rmsd"))
        relax_score = _first_csv_float(sample, ("relax_score", "relax", "score_per_residue"))
        predicted_plddt = _first_csv_float(sample, ("predicted_plddt", "prediction", "acquisition_score"))
        round_num = _first_csv_int(sample, ("round", "evolution_round"))
        selection_rank = _first_csv_int(sample, ("selection_rank", "rank"))

        component_scores: dict[str, float] = {}
        if soluprot is not None:
            component_scores["soluprot"] = _clamp01(soluprot)
        if plddt is not None:
            component_scores["plddt"] = _clamp01(plddt / 100.0)
        if rmsd is not None:
            component_scores["rmsd"] = 1.0 - _clamp01(rmsd / max(1e-6, float(rmsd_ref)))

        used_weight = 0.0
        weighted_sum = 0.0
        for key in scored_weight_keys:
            score = component_scores.get(key)
            if score is None:
                continue
            weight = max(0.0, float(weights.get(key, 0.0)))
            if weight <= 0.0:
                continue
            used_weight += weight
            weighted_sum += weight * score
        score_norm = (weighted_sum / used_weight) if used_weight > 0 else None
        coverage = (used_weight / total_weight) if total_weight > 0 else 0.0
        composite_score = (score_norm * 100.0 * coverage) if score_norm is not None else None
        phase = str(sample.get("phase") or "").strip()

        rows.append(
            {
                "seq_id": seq_id,
                "tier": None,
                "source": "evolution",
                "sequence": str(sample.get("sequence") or "").strip(),
                "soluprot": soluprot,
                "plddt": plddt,
                "rmsd": rmsd,
                "rmsd_target": None,
                "relax": relax_score,
                "wt_identity": None,
                "wt_identity_pct": None,
                "wt_diff_count": None,
                "wt_compare_len": None,
                "wt_diff_ratio": None,
                "wt_diff_pct": None,
                "novelty": None,
                "soluprot_passed": soluprot is not None,
                "af2_candidate": True,
                "af2_selected": plddt is not None,
                "relax_selected": relax_score is not None,
                "component_scores": component_scores,
                "score_norm": score_norm,
                "score": composite_score,
                "coverage": coverage,
                "af2_ranked_pdb_path": _evolution_design_artifact_path(run_root, seq_id),
                "evolution_round": round_num,
                "evolution_phase": phase or None,
                "evolution_selection_rank": selection_rank,
                "evolution_surrogate_model": surrogate_model,
                "evolution_predicted_plddt": predicted_plddt,
            }
        )

    rows.sort(
        key=lambda row: (
            row.get("score") is not None,
            float(row.get("score") or 0.0),
            float(row.get("plddt") or 0.0),
            -float(row.get("rmsd") or 0.0),
            float(row.get("soluprot") or 0.0),
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def _read_csv_dict_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _csv_float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _csv_int_or_none(value: object) -> int | None:
    num = _csv_float_or_none(value)
    if num is None:
        return None
    try:
        return int(num)
    except Exception:
        return None


def _csv_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _surrogate_tier_key(value: object) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    try:
        num = float(text)
    except Exception:
        return text
    if num <= 1.0:
        return _tier_key(num)
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return str(num).rstrip("0").rstrip(".")


def _surrogate_row_key(tier: object, seq_id: object) -> tuple[str, str]:
    return (_surrogate_tier_key(tier), str(seq_id or "").strip())


def _normalize_surrogate_cv_row(row: dict[str, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in row.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if key_text in {
            "selection_score",
            "spearman",
            "kendall",
            "mae",
            "rmse",
            "top_quartile_precision",
            "top_quartile_enrichment",
        }:
            out[key_text] = _csv_float_or_none(value)
        elif key_text in {"n_labels", "cv_folds"}:
            out[key_text] = _csv_int_or_none(value)
        else:
            out[key_text] = value
    return out


def _normalize_surrogate_top_row(row: dict[str, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in row.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if key_text in {"rank"}:
            out[key_text] = _csv_int_or_none(value)
        elif key_text in {"tier"}:
            out[key_text] = _csv_float_or_none(value)
        elif key_text in {"acquisition_score", "af2_label"}:
            out[key_text] = _csv_float_or_none(value)
        else:
            out[key_text] = value
    return out


def _surrogate_models_from_prediction_header(rows: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    if not rows:
        return seen
    for key in rows[0].keys():
        key_text = str(key or "").strip()
        if key_text.startswith("prediction_"):
            model = key_text.removeprefix("prediction_")
            if model and model not in seen:
                seen.append(model)
    return seen


def _load_surrogate_triage_context(run_root: Path) -> dict[str, object] | None:
    triage_dir = run_root / "surrogate_triage"
    selection_path = triage_dir / "model_selection.json"
    selection = _load_json_file(selection_path)
    if not isinstance(selection, dict):
        return None

    training_ids = {
        str(item or "").strip()
        for item in selection.get("training_ids", [])
        if str(item or "").strip()
    }
    selected_top_ids = {
        str(item or "").strip()
        for item in selection.get("selected_top_ids", [])
        if str(item or "").strip()
    }
    evaluated_ids = {
        str(item or "").strip()
        for item in selection.get("evaluated_ids", [])
        if str(item or "").strip()
    }

    cv_metrics = [
        _normalize_surrogate_cv_row(row)
        for row in _read_csv_dict_rows(triage_dir / "cv_metrics.csv")
    ]
    top_rows = [
        _normalize_surrogate_top_row(row)
        for row in _read_csv_dict_rows(triage_dir / "acquired_topk.csv")
    ]
    prediction_rows = _read_csv_dict_rows(triage_dir / "model_predictions.csv")
    selected_policy = str(selection.get("selected_policy") or selection.get("model") or "").strip()
    prediction_models = _surrogate_models_from_prediction_header(prediction_rows)
    row_meta_by_key: dict[tuple[str, str], dict[str, object]] = {}

    selected_top_key_by_global: dict[str, int | None] = {}
    for top_row in top_rows:
        global_id = str(top_row.get("global_seq_id") or "").strip()
        if global_id:
            selected_top_key_by_global[global_id] = (
                int(top_row["rank"]) if isinstance(top_row.get("rank"), int) else None
            )
        key = _surrogate_row_key(top_row.get("tier"), top_row.get("seq_id"))
        if not key[0] or not key[1]:
            continue
        meta = row_meta_by_key.setdefault(key, {})
        meta.update(
            {
                "surrogate_role": "top_k",
                "surrogate_rank": top_row.get("rank"),
                "surrogate_global_seq_id": top_row.get("global_seq_id"),
                "surrogate_acquisition_policy": top_row.get("acquisition_policy"),
                "surrogate_acquisition_score": top_row.get("acquisition_score"),
                "surrogate_af2_label": top_row.get("af2_label"),
                "surrogate_selected_model": selected_policy or top_row.get("acquisition_policy"),
            }
        )

    ids_to_keep = set(training_ids) | set(selected_top_ids) | set(evaluated_ids)
    for pred_row in prediction_rows:
        global_id = str(pred_row.get("global_seq_id") or "").strip()
        acquired = _csv_bool(pred_row.get("acquired")) is True
        if ids_to_keep and global_id not in ids_to_keep and not acquired:
            continue
        key = _surrogate_row_key(pred_row.get("tier"), pred_row.get("seq_id"))
        if not key[0] or not key[1]:
            continue
        meta = row_meta_by_key.setdefault(key, {})
        split = str(pred_row.get("split") or "").strip()
        role = str(meta.get("surrogate_role") or "").strip()
        if not role:
            if global_id in selected_top_ids or acquired:
                role = "top_k"
            elif global_id in training_ids or split == "training":
                role = "training"
            elif evaluated_ids and global_id in evaluated_ids:
                role = "evaluated"
        if role:
            meta["surrogate_role"] = role
        meta["surrogate_global_seq_id"] = global_id
        meta["surrogate_split"] = split
        meta["surrogate_acquired"] = acquired
        meta["surrogate_af2_label"] = _csv_float_or_none(pred_row.get("af2_label"))
        meta["surrogate_selected_model"] = selected_policy
        if selected_policy:
            meta["surrogate_selected_prediction"] = _csv_float_or_none(
                pred_row.get(f"prediction_{selected_policy}")
            )
            meta["surrogate_selected_rank"] = _csv_int_or_none(
                pred_row.get(f"rank_{selected_policy}")
            )
        for model in prediction_models:
            prediction = _csv_float_or_none(pred_row.get(f"prediction_{model}"))
            rank = _csv_int_or_none(pred_row.get(f"rank_{model}"))
            if prediction is not None:
                meta[f"surrogate_prediction_{model}"] = prediction
            if rank is not None:
                meta[f"surrogate_rank_{model}"] = rank
        if meta.get("surrogate_rank") is None and global_id in selected_top_key_by_global:
            meta["surrogate_rank"] = selected_top_key_by_global.get(global_id)

    role_counts: dict[str, int] = {}
    for meta in row_meta_by_key.values():
        role = str(meta.get("surrogate_role") or "").strip()
        if role:
            role_counts[role] = role_counts.get(role, 0) + 1

    public = {
        "enabled": bool(selection.get("enabled", True)),
        "model": selection.get("model"),
        "requested_policy": selection.get("model"),
        "selected_policy": selected_policy,
        "selection_strategy": selection.get("selection_strategy"),
        "models": selection.get("models") if isinstance(selection.get("models"), list) else prediction_models,
        "comparator_models": selection.get("comparator_models")
        if isinstance(selection.get("comparator_models"), list)
        else [],
        "ensemble_models": selection.get("ensemble_models")
        if isinstance(selection.get("ensemble_models"), list)
        else [],
        "fitted_models": selection.get("fitted_models")
        if isinstance(selection.get("fitted_models"), list)
        else [],
        "fit_errors": selection.get("fit_errors") if isinstance(selection.get("fit_errors"), dict) else {},
        "initial_samples": selection.get("initial_samples"),
        "top_k": selection.get("top_k"),
        "cv_folds": selection.get("cv_folds"),
        "candidate_count_before_triage": selection.get("candidate_count_before_triage"),
        "candidate_count_after_triage": selection.get("candidate_count_after_triage"),
        "candidate_count_after_budget": selection.get("candidate_count_after_budget"),
        "expected_af2_calls": selection.get("expected_af2_calls"),
        "training_count": len(training_ids) or role_counts.get("training", 0),
        "selected_top_count": len(selected_top_ids) or len(top_rows) or role_counts.get("top_k", 0),
        "evaluated_count": len(evaluated_ids)
        or sum(role_counts.get(role, 0) for role in ("top_k", "training", "evaluated")),
        "cv_metrics": cv_metrics,
        "top_rows": top_rows[:200],
        "artifacts": {
            "model_selection": "surrogate_triage/model_selection.json",
            "cv_metrics": "surrogate_triage/cv_metrics.csv",
            "model_predictions": "surrogate_triage/model_predictions.csv",
            "acquired_topk": "surrogate_triage/acquired_topk.csv",
            "model_comparison": "surrogate_triage/model_comparison.svg",
        },
    }
    return {"public": public, "row_meta_by_key": row_meta_by_key}


def _apply_surrogate_triage_to_hit_rows(
    rows: list[dict[str, object]],
    context: dict[str, object] | None,
    *,
    include_surrogate_pool: bool = False,
) -> list[dict[str, object]]:
    if not context:
        return rows
    raw_meta = context.get("row_meta_by_key")
    if not isinstance(raw_meta, dict) or not raw_meta:
        return rows
    keep_roles = {"top_k", "training", "evaluated"}
    out: list[dict[str, object]] = []
    for row in rows:
        key = _surrogate_row_key(row.get("tier"), row.get("seq_id"))
        meta = raw_meta.get(key)
        if not isinstance(meta, dict):
            if include_surrogate_pool:
                out.append(row)
            continue
        annotated = dict(row)
        annotated["backbone_source"] = row.get("source")
        annotated.update(meta)
        if str(annotated.get("surrogate_role") or "") in keep_roles:
            annotated["source"] = "surrogate"
        if include_surrogate_pool or str(annotated.get("surrogate_role") or "") in keep_roles:
            out.append(annotated)

    def sort_key(row: dict[str, object]) -> tuple[int, int, float, float, float]:
        role = str(row.get("surrogate_role") or "")
        role_order = {"top_k": 0, "training": 1, "evaluated": 2}.get(role, 3)
        surrogate_rank = row.get("surrogate_rank")
        rank_num = int(surrogate_rank) if isinstance(surrogate_rank, int) else 10**9
        prediction = (
            float(row.get("surrogate_selected_prediction"))
            if isinstance(row.get("surrogate_selected_prediction"), (int, float))
            else float(row.get("score") or -1.0)
        )
        plddt = float(row.get("plddt") or -1.0)
        score = float(row.get("score") or -1.0)
        return (role_order, rank_num, -prediction, -plddt, -score)

    out.sort(key=sort_key)
    for idx, row in enumerate(out, start=1):
        row["rank"] = idx
    return out


def _hit_list_stats(rows: list[dict[str, object]]) -> dict[str, object]:
    score_values = [
        float(row["score"])
        for row in rows
        if isinstance(row.get("score"), (int, float))
    ]
    plddt_values = [
        float(row["plddt"])
        for row in rows
        if isinstance(row.get("plddt"), (int, float))
    ]
    rmsd_values = [
        float(row["rmsd"]) for row in rows if isinstance(row.get("rmsd"), (int, float))
    ]
    sol_values = [
        float(row["soluprot"])
        for row in rows
        if isinstance(row.get("soluprot"), (int, float))
    ]
    relax_values = [
        float(row["relax"])
        for row in rows
        if isinstance(row.get("relax"), (int, float))
    ]
    return {
        "count": len(rows),
        "score_median": _median(score_values),
        "score_p90": _percentile_from_sorted(sorted(score_values), 0.90)
        if score_values
        else None,
        "plddt_median": _median(plddt_values),
        "rmsd_median": _median(rmsd_values),
        "soluprot_median": _median(sol_values),
        "relax_median": _median(relax_values),
    }


def _get_hit_list(runner: PipelineRunner, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    root = resolve_run_path(runner.output_root, run_id)
    if not root.exists():
        raise ValueError("run_id not found")
    limit = max(1, _as_int(arguments.get("limit"), 120))
    min_score = _as_float(arguments.get("min_score"), 0.0)
    rmsd_ref = max(0.1, _as_float(arguments.get("rmsd_ref"), 5.0))
    request, summary = _load_run_request_summary(root)
    comparison_summary = _build_comparison_summary(
        run_root=root, request=request, summary=summary
    )
    weights = _normalize_hit_weights(arguments.get("weights"))
    rows = _build_hit_list_rows(
        run_root=root,
        request=request,
        summary=summary,
        weights=weights,
        rmsd_ref=rmsd_ref,
    )
    if not rows:
        rows = _build_evolution_hit_list_rows(
            run_root=root,
            summary=summary,
            weights=weights,
            rmsd_ref=rmsd_ref,
        )
    surrogate_context = _load_surrogate_triage_context(root)
    rows = _apply_surrogate_triage_to_hit_rows(
        rows,
        surrogate_context,
        include_surrogate_pool=_as_bool(arguments.get("include_surrogate_pool"), False),
    )
    filtered = [
        row
        for row in rows
        if (row.get("score") is None and min_score <= 0.0)
        or (
            isinstance(row.get("score"), (int, float))
            and float(row.get("score")) >= float(min_score)
        )
    ]
    sliced = filtered[:limit]
    return {
        "run_id": run_id,
        "generated_at": _now_iso(),
        "weights": weights,
        "min_score": float(min_score),
        "rmsd_ref": float(rmsd_ref),
        "relax_enabled": bool(request.get("relax_enabled") is True)
        if isinstance(request, dict)
        else False,
        "total_rows": len(rows),
        "filtered_rows": len(filtered),
        "rows": sliced,
        "stats": _hit_list_stats(filtered),
        "completeness": _completeness_flags(comparison_summary),
        "surrogate_triage": surrogate_context.get("public")
        if isinstance(surrogate_context, dict)
        else None,
    }


def _csv_escape(value: object) -> str:
    text = str(value if value is not None else "")
    if any(ch in text for ch in [",", '"', "\n", "\r"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def _to_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


_EXPORT_INLINE_OMIT_KEYS = {
    "archive_base64",
    "base64",
    "ranked_0_pdb",
    "unrelaxed_pdb",
    "pdb_text",
    "cif_gz_base64",
    "out_dir_zip_b64",
    "zip_b64",
    "a3m_gz_b64",
    "embeddings_npz_b64",
}
_EXPORT_INLINE_OMIT_SUFFIXES = ("_base64", "_b64")
_EXPORT_MAX_INLINE_STRING_CHARS = 1_000_000


def _omit_export_inline_payload(value: object) -> dict[str, object]:
    return {
        "omitted": True,
        "reason": "large inline payload stored as artifact output",
        "chars": len(value) if isinstance(value, str) else None,
    }


def _strip_export_inline_payloads(value: object, *, key: str = "") -> object:
    normalized_key = str(key or "").strip().lower()
    if isinstance(value, dict):
        return {
            str(item_key): _strip_export_inline_payloads(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_strip_export_inline_payloads(item) for item in value]
    if isinstance(value, str):
        if (
            normalized_key in _EXPORT_INLINE_OMIT_KEYS
            or normalized_key.endswith(_EXPORT_INLINE_OMIT_SUFFIXES)
            or len(value) > _EXPORT_MAX_INLINE_STRING_CHARS
        ):
            return _omit_export_inline_payload(value)
    return value


def _export_results_package(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    run_root = resolve_run_path(runner.output_root, run_id)
    if not run_root.exists():
        raise ValueError("run_id not found")

    include_top_n = max(1, min(200, _as_int(arguments.get("include_top_n"), 10)))
    weights = _normalize_hit_weights(arguments.get("weights"))
    request, summary = _load_run_request_summary(run_root)
    comparison_summary = _build_comparison_summary(
        run_root=run_root, request=request, summary=summary
    )
    hit_rows = _build_hit_list_rows(
        run_root=run_root,
        request=request,
        summary=summary,
        weights=weights,
        rmsd_ref=max(0.1, _as_float(arguments.get("rmsd_ref"), 5.0)),
    )
    top_rows = hit_rows[:include_top_n]

    export_dir = ensure_dir(run_root / "exports")
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    zip_name = f"result_package_{stamp}.zip"
    zip_path = export_dir / zip_name
    if zip_path.exists():
        zip_name = f"result_package_{stamp}_{uuid.uuid4().hex[:8]}.zip"
        zip_path = export_dir / zip_name

    included: list[str] = []

    def _write_file_if_exists(
        zf: zipfile.ZipFile, rel_path: str, arcname: str | None = None
    ) -> None:
        src = run_root / rel_path
        if not src.exists() or not src.is_file():
            return
        name = arcname or rel_path
        zf.write(src, arcname=name)
        included.append(name)

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in [
            "report.md",
            "report_ko.md",
            "comparisons.json",
            "request.json",
            "status.json",
            "backbones.json",
            "wt/metrics.json",
            "wt/af2/metrics.json",
            "wt/af2/ranked_0.pdb",
        ]:
            _write_file_if_exists(zf, rel)
        if "comparisons.json" not in included:
            zf.writestr("comparisons.json", _to_json_bytes(comparison_summary))
            included.append("comparisons.json")
        if isinstance(summary, dict):
            zf.writestr(
                "summary.json", _to_json_bytes(_strip_export_inline_payloads(summary))
            )
            included.append("summary.json")

        root_surrogate_dir = run_root / "surrogate_triage"
        if root_surrogate_dir.exists() and root_surrogate_dir.is_dir():
            for path in sorted(p for p in root_surrogate_dir.rglob("*") if p.is_file()):
                rel = path.relative_to(run_root).as_posix()
                _write_file_if_exists(zf, rel)

        tiers_root = run_root / "tiers"
        if tiers_root.exists() and tiers_root.is_dir():
            for tier_dir in sorted(
                [p for p in tiers_root.iterdir() if p.is_dir()], key=lambda p: p.name
            ):
                tier_name = tier_dir.name
                for name in ["soluprot.json", "af2_scores.json", "novelty.tsv"]:
                    rel = f"tiers/{tier_name}/{name}"
                    _write_file_if_exists(zf, rel)
                surrogate_dir = tier_dir / "surrogate_triage"
                if surrogate_dir.exists() and surrogate_dir.is_dir():
                    for path in sorted(p for p in surrogate_dir.rglob("*") if p.is_file()):
                        rel = path.relative_to(run_root).as_posix()
                        _write_file_if_exists(zf, rel)

        zf.writestr(
            "tables/hit_list_full.json",
            _to_json_bytes({"rows": hit_rows, "weights": weights}),
        )
        included.append("tables/hit_list_full.json")
        zf.writestr(
            "tables/hit_list_top.json",
            _to_json_bytes({"rows": top_rows, "weights": weights}),
        )
        included.append("tables/hit_list_top.json")

        csv_header = [
            "rank",
            "seq_id",
            "tier",
            "source",
            "score",
            "soluprot",
            "plddt",
            "rmsd",
            "wt_identity",
            "wt_identity_pct",
            "wt_diff_count",
            "wt_compare_len",
            "wt_diff_ratio",
            "wt_diff_pct",
            "novelty",
            "soluprot_passed",
            "af2_selected",
            "af2_ranked_pdb_path",
        ]
        csv_lines = [",".join(csv_header)]
        for row in top_rows:
            csv_lines.append(
                ",".join(
                    _csv_escape(
                        row.get(
                            key
                            if key != "af2_ranked_pdb_path"
                            else "af2_ranked_pdb_path"
                        )
                    )
                    for key in csv_header
                )
            )
        csv_payload = ("\n".join(csv_lines) + "\n").encode("utf-8")
        zf.writestr("tables/hit_list_top.csv", csv_payload)
        included.append("tables/hit_list_top.csv")

        for row in top_rows:
            pdb_rel = row.get("af2_ranked_pdb_path")
            if not isinstance(pdb_rel, str) or not pdb_rel.strip():
                continue
            _write_file_if_exists(zf, pdb_rel)

        manifest = {
            "run_id": run_id,
            "generated_at": _now_iso(),
            "weights": weights,
            "include_top_n": include_top_n,
            "included_count": len(included),
            "included_files": included,
            "completeness": _completeness_flags(comparison_summary),
        }
        zf.writestr("manifest.json", _to_json_bytes(manifest))
        included.append("manifest.json")

    rel_path = f"exports/{zip_name}"
    return {
        "run_id": run_id,
        "path": rel_path,
        "filename": zip_name,
        "download_url": f"/runs/{run_id}/exports/{zip_name}",
        "size_bytes": zip_path.stat().st_size,
        "included_count": len(included),
        "include_top_n": include_top_n,
        "weights": weights,
    }


def pipeline_request_from_args(
    args: dict[str, Any], *, strict_target: bool = True
) -> PipelineRequest:
    target_fasta = _as_text(args.get("target_fasta"))
    target_pdb = _as_text(resolve_structure_input(args.get("target_pdb")))
    evolution_mode = _as_bool(args.get("evolution_mode"), False)
    evolution_initial_samples = _as_int(args.get("evolution_initial_samples"), 30)
    evolution_rounds = _as_int(args.get("evolution_rounds"), 4)
    evolution_samples_per_round = _as_int(args.get("evolution_samples_per_round"), 20)
    evolution_pool_size = _as_int(args.get("evolution_pool_size"), 1000)
    evolution_oracle_samples = _as_int(args.get("evolution_oracle_samples"), 20)
    evolution_label_source = (
        _as_text(args.get("evolution_label_source")).strip().lower()
        or "experimental"
    )
    if evolution_label_source in {"af2", "computational", "in_silico"}:
        evolution_label_source = "in_silico_af2"
    if evolution_label_source not in {"experimental", "in_silico_af2"}:
        raise ValueError(
            "evolution_label_source must be one of: experimental, in_silico_af2"
        )
    evolution_objective_metric = (
        _as_text(args.get("evolution_objective_metric")).strip() or "activity"
    )
    evolution_experiment_source_run_id = (
        _as_text(args.get("evolution_experiment_source_run_id")).strip() or None
    )
    evolution_surrogate_model = (
        _as_text(args.get("evolution_surrogate_model")).strip() or "rf"
    )
    use_memory_bank = _as_bool(args.get("use_memory_bank"), False)
    surrogate_triage_enabled = _as_bool(args.get("surrogate_triage_enabled"), False)
    surrogate_triage_scope = (
        _as_text(args.get("surrogate_triage_scope")).strip().lower() or "per_tier"
    )
    if surrogate_triage_scope not in {"per_tier", "pooled_tiers"}:
        raise ValueError("surrogate_triage_scope must be one of: per_tier, pooled_tiers")
    surrogate_triage_initial_samples = _as_int(
        args.get("surrogate_triage_initial_samples"), 30
    )
    surrogate_triage_top_k = _as_int(args.get("surrogate_triage_top_k"), 20)
    surrogate_triage_model = _as_model_name_selection(
        args.get("surrogate_triage_model"), default="auto"
    )
    surrogate_triage_comparator_models = _as_model_name_selection(
        args.get("surrogate_triage_comparator_models"),
        default=["rf", "ridge", "lightgbm", "xgboost"],
    )
    surrogate_triage_ensemble_models = _as_model_name_selection(
        args.get("surrogate_triage_ensemble_models"),
        default=[],
    )
    surrogate_triage_cv_folds = _as_int(args.get("surrogate_triage_cv_folds"), 5)
    project_id = _as_text(args.get("project_id")).strip() or None
    round_id = _as_text(args.get("round_id")).strip() or None
    rfd3_inputs = _as_dict(args.get("rfd3_inputs"), name="rfd3_inputs")
    rfd3_inputs_text = _as_text(args.get("rfd3_inputs_text")).strip() or None
    rfd3_contig = _as_str_or_list(args.get("rfd3_contig"))
    rfd3_input_files = _as_dict_str(
        args.get("rfd3_input_files"), name="rfd3_input_files"
    )
    rfd3_input_pdb = _as_text(resolve_structure_input(args.get("rfd3_input_pdb"))).strip() or None
    rfd3_use_raw = args.get("rfd3_use")
    rfd3_mode = _as_text(args.get("rfd3_mode")).strip() or None
    rfd3_hotspots = _as_str_or_list(args.get("rfd3_hotspots"))
    rfd3_infer_ori_strategy = (
        _as_text(args.get("rfd3_infer_ori_strategy")).strip() or None
    )
    rfd3_is_non_loopy = (
        _as_bool(args.get("rfd3_is_non_loopy"), False)
        if args.get("rfd3_is_non_loopy") is not None
        and str(args.get("rfd3_is_non_loopy")).strip() != ""
        else None
    )
    rfd3_unindex = _as_str_or_list(args.get("rfd3_unindex"))
    rfd3_length = _as_str_or_list(args.get("rfd3_length"))
    rfd3_select_fixed_atoms = _as_rfd3_select_fixed_atoms(
        args.get("rfd3_select_fixed_atoms")
    )
    rfd3_ligand = _as_str_or_list(args.get("rfd3_ligand"))
    rfd3_select_unfixed_sequence = (
        _as_text(args.get("rfd3_select_unfixed_sequence")).strip() or None
    )
    rfd3_cli_args = _as_text(args.get("rfd3_cli_args")).strip() or None
    rfd3_env = _as_dict_str(args.get("rfd3_env"), name="rfd3_env")
    rfd3_design_index = _as_int(args.get("rfd3_design_index"), 0)
    rfd3_use_ensemble = _as_bool(args.get("rfd3_use_ensemble"), False)
    rfd3_max_return_designs = _as_int(args.get("rfd3_max_return_designs"), 10)
    rfd3_partial_t = (
        _as_float(args.get("rfd3_partial_t"), 0.0)
        if str(args.get("rfd3_partial_t") or "").strip()
        else None
    )
    rfd3_sampling_strategy = (
        _as_text(args.get("rfd3_sampling_strategy")).strip() or None
    )
    rfd3_fail_on_duplicate_backbones = _as_bool(
        args.get("rfd3_fail_on_duplicate_backbones"), False
    )
    rfd3_target_rmsd_cutoff_raw = args.get("rfd3_target_rmsd_cutoff")
    rfd3_target_rmsd_cutoff_specified = (
        "rfd3_target_rmsd_cutoff" in args
        and rfd3_target_rmsd_cutoff_raw is not None
        and not (
            isinstance(rfd3_target_rmsd_cutoff_raw, str)
            and not rfd3_target_rmsd_cutoff_raw.strip()
        )
    )
    rfd3_target_rmsd_cutoff = (
        _as_float(rfd3_target_rmsd_cutoff_raw, 0.0)
        if rfd3_target_rmsd_cutoff_specified
        else 2.0
    )
    rfd3_max_attempted_designs = (
        _as_int(args.get("rfd3_max_attempted_designs"), 0)
        if str(args.get("rfd3_max_attempted_designs") or "").strip()
        else None
    )

    bioemu_use = _as_bool(args.get("bioemu_use"), False)
    bioemu_sequence = _as_text(args.get("bioemu_sequence")).strip() or None
    bioemu_batch_size_100 = (
        _as_int(args.get("bioemu_batch_size_100"), 50)
        if str(args.get("bioemu_batch_size_100") or "").strip()
        else None
    )
    bioemu_model_name = str(args.get("bioemu_model_name") or "bioemu-v1.1")
    bioemu_filter_samples = _as_bool(args.get("bioemu_filter_samples"), True)
    bioemu_base_seed = (
        _as_int(args.get("bioemu_base_seed"), 0)
        if str(args.get("bioemu_base_seed") or "").strip()
        else None
    )
    bioemu_steering_config_text = (
        _as_text(args.get("bioemu_steering_config_text")).strip() or None
    )
    bioemu_max_return_structures = _as_int(args.get("bioemu_max_return_structures"), 10)
    bioemu_target_rmsd_cutoff_raw = args.get("bioemu_target_rmsd_cutoff")
    bioemu_target_rmsd_cutoff_specified = (
        "bioemu_target_rmsd_cutoff" in args
        and bioemu_target_rmsd_cutoff_raw is not None
        and not (
            isinstance(bioemu_target_rmsd_cutoff_raw, str)
            and not bioemu_target_rmsd_cutoff_raw.strip()
        )
    )
    bioemu_target_rmsd_cutoff = (
        _as_float(bioemu_target_rmsd_cutoff_raw, 0.0)
        if bioemu_target_rmsd_cutoff_specified
        else 2.0
    )
    backbone_filter_use_dssp = _as_bool(args.get("backbone_filter_use_dssp"), True)
    bioemu_max_attempted_structures = (
        _as_int(args.get("bioemu_max_attempted_structures"), 0)
        if str(args.get("bioemu_max_attempted_structures") or "").strip()
        else None
    )
    requested_return_count = max(1, int(bioemu_max_return_structures))
    if str(args.get("bioemu_num_samples") or "").strip():
        bioemu_num_samples = _as_int(
            args.get("bioemu_num_samples"), bioemu_max_return_structures
        )
    else:
        bioemu_num_samples = _recommended_bioemu_num_samples(
            requested_return_count, bioemu_filter_samples
        )
    if bioemu_max_attempted_structures is None:
        bioemu_max_attempted_structures = _recommended_bioemu_max_attempted_structures(
            requested_return_count,
            bioemu_filter_samples,
        )
    bioemu_env = _as_dict_str(args.get("bioemu_env"), name="bioemu_env")

    diffdock_ligand_smiles = (
        _as_text(args.get("diffdock_ligand_smiles")).strip() or None
    )
    diffdock_ligand_sdf = _as_text(args.get("diffdock_ligand_sdf")).strip() or None
    diffdock_config = str(args.get("diffdock_config") or "default_inference_args.yaml")
    diffdock_extra_args = _as_text(args.get("diffdock_extra_args")).strip() or None
    diffdock_cuda_visible_devices = (
        _as_text(args.get("diffdock_cuda_visible_devices")).strip() or None
    )

    legacy_rfd3_requested = bool(
        rfd3_inputs_text
        or rfd3_inputs
        or rfd3_input_pdb
        or rfd3_input_files
        or rfd3_contig
        or rfd3_mode
        or rfd3_hotspots
        or rfd3_infer_ori_strategy
        or rfd3_is_non_loopy is not None
        or rfd3_unindex
        or rfd3_length
        or rfd3_select_fixed_atoms
        or rfd3_ligand
        or rfd3_select_unfixed_sequence
        or rfd3_cli_args
        or rfd3_env
        or (rfd3_design_index != 0)
        or rfd3_partial_t is not None
        or rfd3_sampling_strategy
        or rfd3_fail_on_duplicate_backbones
        or rfd3_target_rmsd_cutoff_specified
        or (rfd3_max_attempted_designs is not None)
        or rfd3_use_ensemble
    )
    if rfd3_use_raw is None or (
        isinstance(rfd3_use_raw, str) and not rfd3_use_raw.strip()
    ):
        rfd3_use = None
    else:
        rfd3_use = _as_bool(rfd3_use_raw, False)
    has_rfd3 = (
        legacy_rfd3_requested
        if rfd3_use is None
        else (bool(rfd3_use) and legacy_rfd3_requested)
    )
    if (
        strict_target
        and not target_fasta.strip()
        and not target_pdb.strip()
        and not has_rfd3
    ):
        raise ValueError("One of target_fasta or target_pdb or rfd3 inputs is required")

    start_from = _canonical_pipeline_stage_arg(args.get("start_from"))
    stop_after = _canonical_pipeline_stage_arg(args.get("stop_after"))
    dry_run = _as_bool(args.get("dry_run"), False)
    agent_panel_enabled = _as_bool(args.get("agent_panel_enabled"), True)
    auto_recover = _as_bool(args.get("auto_recover"), True)
    wt_compare = _as_bool(args.get("wt_compare"), True)
    mask_consensus_apply = _as_bool(args.get("mask_consensus_apply"), False)

    design_chains = _as_list_of_str(args.get("design_chains"))
    fixed_positions_extra = _as_fixed_positions_extra(args.get("fixed_positions_extra"))
    conservation_tiers = _as_list_of_float(args.get("conservation_tiers"))
    selected_tiers = _as_list_of_float(args.get("selected_tiers"))
    ligand_resnames = _as_list_of_str(args.get("ligand_resnames"))
    ligand_atom_chains = _as_list_of_str(args.get("ligand_atom_chains"))
    af2_sequence_ids = _as_list_of_str(args.get("af2_sequence_ids"))
    af2_provider = _as_af2_provider(args.get("af2_provider"), "colabfold")
    surface_only = _as_bool(args.get("surface_only"), False)
    ligand_mask_use_original_target = _as_bool(
        args.get("ligand_mask_use_original_target"), True
    )
    novelty_enabled = _as_bool(args.get("novelty_enabled"), True)
    surface_min_rel = _as_float(args.get("surface_min_rel"), 0.2)
    surface_min_abs = _as_float(args.get("surface_min_abs"), 10.0)
    pi_min = (
        _as_float(args.get("pi_min"), 0.0)
        if str(args.get("pi_min") or "").strip()
        else None
    )
    pi_max = (
        _as_float(args.get("pi_max"), 0.0)
        if str(args.get("pi_max") or "").strip()
        else None
    )
    af2_max_candidates_per_tier = (
        _as_int(args.get("af2_max_candidates_per_tier"), 0)
        if str(args.get("af2_max_candidates_per_tier") or "").strip()
        else 0
    )

    return PipelineRequest(
        target_fasta=target_fasta,
        target_pdb=target_pdb,
        evolution_mode=evolution_mode,
        evolution_initial_samples=evolution_initial_samples,
        evolution_rounds=evolution_rounds,
        evolution_samples_per_round=evolution_samples_per_round,
        evolution_pool_size=evolution_pool_size,
        evolution_oracle_samples=evolution_oracle_samples,
        evolution_label_source=evolution_label_source,
        evolution_objective_metric=evolution_objective_metric,
        evolution_experiment_source_run_id=evolution_experiment_source_run_id,
        evolution_surrogate_model=evolution_surrogate_model,
        use_memory_bank=use_memory_bank,
        surrogate_triage_enabled=surrogate_triage_enabled,
        surrogate_triage_scope=surrogate_triage_scope,
        surrogate_triage_initial_samples=max(1, int(surrogate_triage_initial_samples)),
        surrogate_triage_top_k=max(1, int(surrogate_triage_top_k)),
        surrogate_triage_model=surrogate_triage_model,
        surrogate_triage_comparator_models=surrogate_triage_comparator_models,
        surrogate_triage_ensemble_models=surrogate_triage_ensemble_models,
        surrogate_triage_cv_folds=max(2, int(surrogate_triage_cv_folds)),
        project_id=project_id,
        round_id=round_id,
        rfd3_use=rfd3_use,
        rfd3_inputs=rfd3_inputs,
        rfd3_inputs_text=rfd3_inputs_text,
        rfd3_input_files=rfd3_input_files,
        rfd3_input_pdb=rfd3_input_pdb,
        rfd3_mode=rfd3_mode,
        rfd3_spec_name=str(args.get("rfd3_spec_name") or "spec-1"),
        rfd3_contig=rfd3_contig,
        rfd3_hotspots=rfd3_hotspots,
        rfd3_infer_ori_strategy=rfd3_infer_ori_strategy,
        rfd3_is_non_loopy=rfd3_is_non_loopy,
        rfd3_unindex=rfd3_unindex,
        rfd3_length=rfd3_length,
        rfd3_select_fixed_atoms=rfd3_select_fixed_atoms,
        rfd3_ligand=rfd3_ligand,
        rfd3_select_unfixed_sequence=rfd3_select_unfixed_sequence,
        rfd3_cli_args=rfd3_cli_args,
        rfd3_env=rfd3_env,
        rfd3_design_index=rfd3_design_index,
        rfd3_use_ensemble=rfd3_use_ensemble,
        rfd3_max_return_designs=max(1, int(rfd3_max_return_designs)),
        rfd3_partial_t=(float(rfd3_partial_t) if rfd3_partial_t is not None else None),
        rfd3_sampling_strategy=rfd3_sampling_strategy,
        rfd3_fail_on_duplicate_backbones=rfd3_fail_on_duplicate_backbones,
        rfd3_target_rmsd_cutoff=(
            float(rfd3_target_rmsd_cutoff)
            if rfd3_target_rmsd_cutoff is not None
            else None
        ),
        rfd3_max_attempted_designs=(
            max(1, int(rfd3_max_attempted_designs))
            if rfd3_max_attempted_designs is not None
            else None
        ),
        backbone_filter_use_dssp=backbone_filter_use_dssp,
        bioemu_use=bioemu_use,
        bioemu_sequence=bioemu_sequence,
        bioemu_num_samples=max(1, int(bioemu_num_samples)),
        bioemu_batch_size_100=(
            int(bioemu_batch_size_100) if bioemu_batch_size_100 is not None else None
        ),
        bioemu_model_name=bioemu_model_name,
        bioemu_filter_samples=bioemu_filter_samples,
        bioemu_base_seed=(
            int(bioemu_base_seed) if bioemu_base_seed is not None else None
        ),
        bioemu_steering_config_text=bioemu_steering_config_text,
        bioemu_max_return_structures=max(1, int(bioemu_max_return_structures)),
        bioemu_target_rmsd_cutoff=(
            float(bioemu_target_rmsd_cutoff)
            if bioemu_target_rmsd_cutoff is not None
            else None
        ),
        bioemu_max_attempted_structures=(
            max(1, int(bioemu_max_attempted_structures))
            if bioemu_max_attempted_structures is not None
            else None
        ),
        bioemu_env=bioemu_env,
        diffdock_ligand_smiles=diffdock_ligand_smiles,
        diffdock_ligand_sdf=diffdock_ligand_sdf,
        diffdock_config=diffdock_config,
        diffdock_extra_args=diffdock_extra_args,
        diffdock_cuda_visible_devices=diffdock_cuda_visible_devices,
        design_chains=design_chains,
        fixed_positions_extra=fixed_positions_extra,
        conservation_tiers=conservation_tiers or [0.3, 0.5, 0.7],
        selected_tiers=selected_tiers or None,
        conservation_mode=str(args.get("conservation_mode") or "quantile"),
        conservation_weighting=str(args.get("conservation_weighting") or "none"),
        conservation_cluster_method=str(
            args.get("conservation_cluster_method") or "linclust"
        ),
        conservation_cluster_min_seq_id=_as_float(
            args.get("conservation_cluster_min_seq_id"), 0.9
        ),
        conservation_cluster_coverage=(
            _as_float(args.get("conservation_cluster_coverage"), 0.0)
            if str(args.get("conservation_cluster_coverage") or "").strip()
            else None
        ),
        conservation_cluster_cov_mode=(
            _as_int(args.get("conservation_cluster_cov_mode"), 0)
            if str(args.get("conservation_cluster_cov_mode") or "").strip()
            else None
        ),
        conservation_cluster_kmer_per_seq=(
            _as_int(args.get("conservation_cluster_kmer_per_seq"), 0)
            if str(args.get("conservation_cluster_kmer_per_seq") or "").strip()
            else None
        ),
        ligand_mask_distance=_as_float(args.get("ligand_mask_distance"), 6.0),
        ligand_resnames=ligand_resnames,
        ligand_atom_chains=ligand_atom_chains,
        ligand_mask_use_original_target=ligand_mask_use_original_target,
        surface_only=surface_only,
        surface_min_rel=surface_min_rel,
        surface_min_abs=surface_min_abs,
        pdb_strip_nonpositive_resseq=_as_bool(
            args.get("pdb_strip_nonpositive_resseq"), True
        ),
        pdb_renumber_resseq_from_1=_as_bool(
            args.get("pdb_renumber_resseq_from_1"), False
        ),
        num_seq_per_tier=_as_int(args.get("num_seq_per_tier"), 2),
        batch_size=_as_int(args.get("batch_size"), 1),
        sampling_temp=_as_float(args.get("sampling_temp"), 0.1),
        seed=_as_int(args.get("seed"), 0),
        soluprot_cutoff=_as_float(args.get("soluprot_cutoff"), 0.5),
        pi_min=pi_min,
        pi_max=pi_max,
        af2_model_preset=str(args.get("af2_model_preset") or "auto"),
        af2_db_preset=str(args.get("af2_db_preset") or "full_dbs"),
        af2_max_template_date=str(args.get("af2_max_template_date") or "2020-05-14"),
        af2_extra_flags=(
            str(args.get("af2_extra_flags")) if args.get("af2_extra_flags") else None
        ),
        af2_provider=af2_provider,
        af2_plddt_cutoff=_as_float(args.get("af2_plddt_cutoff"), 85.0),
        af2_rmsd_cutoff=_as_float(args.get("af2_rmsd_cutoff"), 2.0),
        af2_max_candidates_per_tier=max(0, int(af2_max_candidates_per_tier)),
        af2_top_k=_as_int(args.get("af2_top_k"), 0),
        af2_sequence_ids=af2_sequence_ids,
        relax_enabled=_as_bool(args.get("relax_enabled"), False),
        relax_score_per_residue_cutoff=(
            _as_float(args.get("relax_score_per_residue_cutoff"), 0.0)
            if str(args.get("relax_score_per_residue_cutoff") or "").strip()
            else None
        ),
        relax_nstruct=max(1, _as_int(args.get("relax_nstruct"), 1)),
        relax_extra_flags=(
            str(args.get("relax_extra_flags"))
            if args.get("relax_extra_flags")
            else None
        ),
        mmseqs_target_db=str(args.get("mmseqs_target_db") or "uniref90"),
        mmseqs_max_seqs=_as_int(args.get("mmseqs_max_seqs"), 3000),
        mmseqs_threads=_as_int(args.get("mmseqs_threads"), 4),
        mmseqs_use_gpu=_as_bool(
            args.get("mmseqs_use_gpu"),
            _env_true("PIPELINE_MMSEQS_USE_GPU") or _env_true("MMSEQS_USE_GPU"),
        ),
        novelty_enabled=novelty_enabled,
        novelty_target_db=str(args.get("novelty_target_db") or "uniref90"),
        msa_min_coverage=_as_float(args.get("msa_min_coverage"), 0.0),
        msa_min_identity=_as_float(args.get("msa_min_identity"), 0.0),
        query_pdb_min_identity=_as_float(args.get("query_pdb_min_identity"), 0.9),
        query_pdb_policy=str(args.get("query_pdb_policy") or "error"),
        start_from=start_from,
        stop_after=stop_after,
        force=_as_bool(args.get("force"), False),
        dry_run=dry_run,
        agent_panel_enabled=agent_panel_enabled,
        auto_recover=auto_recover,
        wt_compare=wt_compare,
        mask_consensus_apply=mask_consensus_apply,
    )


def _pipeline_run_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target_fasta": {"type": "string"},
            "target_pdb": {"type": "string"},
            "rfd3_use": {"type": "boolean"},
            "rfd3_inputs": {"type": "object"},
            "rfd3_inputs_text": {"type": "string"},
            "rfd3_input_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "rfd3_input_pdb": {"type": "string"},
            "rfd3_mode": {
                "type": "string",
                "enum": [
                    "legacy_contig",
                    "binder",
                    "enzyme",
                    "local_diversify",
                    "advanced",
                ],
            },
            "rfd3_spec_name": {"type": "string"},
            "rfd3_contig": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "rfd3_hotspots": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "rfd3_infer_ori_strategy": {"type": "string"},
            "rfd3_is_non_loopy": {"type": "boolean"},
            "rfd3_unindex": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "rfd3_length": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "rfd3_select_fixed_atoms": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "object", "additionalProperties": {"type": "string"}},
                ]
            },
            "rfd3_ligand": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "rfd3_select_unfixed_sequence": {"type": "string"},
            "rfd3_cli_args": {"type": "string"},
            "rfd3_env": {"type": "object", "additionalProperties": {"type": "string"}},
            "rfd3_design_index": {"type": "integer"},
            "rfd3_use_ensemble": {"type": "boolean"},
            "rfd3_max_return_designs": {"type": "integer"},
            "rfd3_partial_t": {"type": "number"},
            "rfd3_sampling_strategy": {"type": "string"},
            "rfd3_fail_on_duplicate_backbones": {"type": "boolean"},
            "rfd3_target_rmsd_cutoff": {"type": "number"},
            "rfd3_max_attempted_designs": {"type": "integer"},
            "backbone_filter_use_dssp": {"type": "boolean"},
            "bioemu_use": {"type": "boolean"},
            "bioemu_sequence": {"type": "string"},
            "bioemu_num_samples": {"type": "integer"},
            "bioemu_batch_size_100": {"type": "integer"},
            "bioemu_model_name": {"type": "string"},
            "bioemu_filter_samples": {"type": "boolean"},
            "bioemu_base_seed": {"type": "integer"},
            "bioemu_steering_config_text": {"type": "string"},
            "bioemu_max_return_structures": {"type": "integer"},
            "bioemu_target_rmsd_cutoff": {"type": "number"},
            "bioemu_max_attempted_structures": {"type": "integer"},
            "bioemu_env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "diffdock_ligand_smiles": {"type": "string"},
            "diffdock_ligand_sdf": {"type": "string"},
            "diffdock_config": {"type": "string"},
            "diffdock_extra_args": {"type": "string"},
            "diffdock_cuda_visible_devices": {"type": "string"},
            "design_chains": {"type": "array", "items": {"type": "string"}},
            "fixed_positions_extra": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": {"type": "integer"}},
                "description": "Extra fixed positions per chain (1-based, query/FASTA numbering). Use '*' to apply to all chains.",
            },
            "conservation_tiers": {"type": "array", "items": {"type": "number"}},
            "selected_tiers": {"type": "array", "items": {"type": "number"}},
            "conservation_mode": {"type": "string", "enum": ["quantile", "threshold"]},
            "conservation_weighting": {"type": "string"},
            "conservation_cluster_method": {"type": "string"},
            "conservation_cluster_min_seq_id": {"type": "number"},
            "conservation_cluster_coverage": {"type": "number"},
            "conservation_cluster_cov_mode": {"type": "integer"},
            "conservation_cluster_kmer_per_seq": {"type": "integer"},
            "ligand_mask_distance": {"type": "number"},
            "ligand_resnames": {"type": "array", "items": {"type": "string"}},
            "ligand_atom_chains": {"type": "array", "items": {"type": "string"}},
            "ligand_mask_use_original_target": {"type": "boolean"},
            "surface_only": {"type": "boolean"},
            "surface_min_rel": {"type": "number"},
            "surface_min_abs": {"type": "number"},
            "pdb_strip_nonpositive_resseq": {"type": "boolean"},
            "pdb_renumber_resseq_from_1": {"type": "boolean"},
            "num_seq_per_tier": {"type": "integer"},
            "batch_size": {"type": "integer"},
            "sampling_temp": {"type": "number"},
            "seed": {"type": "integer"},
            "soluprot_cutoff": {"type": "number"},
            "pi_min": {"type": "number"},
            "pi_max": {"type": "number"},
            "af2_model_preset": {"type": "string"},
            "af2_db_preset": {"type": "string"},
            "af2_max_template_date": {"type": "string"},
            "af2_extra_flags": {"type": "string"},
            "af2_provider": {"type": "string", "enum": ["colabfold", "af2"]},
            "af2_plddt_cutoff": {"type": "number"},
            "af2_rmsd_cutoff": {"type": "number"},
            "af2_max_candidates_per_tier": {"type": "integer"},
            "af2_top_k": {"type": "integer"},
            "af2_sequence_ids": {"type": "array", "items": {"type": "string"}},
            "relax_enabled": {"type": "boolean"},
            "relax_score_per_residue_cutoff": {"type": "number"},
            "relax_nstruct": {"type": "integer"},
            "relax_extra_flags": {"type": "string"},
            "mmseqs_target_db": {"type": "string"},
            "mmseqs_max_seqs": {"type": "integer"},
            "mmseqs_threads": {"type": "integer"},
            "mmseqs_use_gpu": {"type": "boolean"},
            "novelty_enabled": {"type": "boolean"},
            "novelty_target_db": {"type": "string"},
            "msa_min_coverage": {"type": "number"},
            "msa_min_identity": {"type": "number"},
            "query_pdb_min_identity": {"type": "number"},
            "query_pdb_policy": {"type": "string", "enum": ["error", "warn", "ignore"]},
            "project_id": {"type": "string"},
            "round_id": {"type": "string"},
            "run_id": {"type": "string"},
            "start_from": {"type": "string"},
            "stop_after": {"type": "string"},
            "force": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "evolution_mode": {
                "type": "boolean",
                "description": "Run multi-round local active-learning evolution mode",
            },
            "evolution_pool_size": {
                "type": "integer",
                "description": "Initial sequences to generate in Stage 1 (default 1000)",
            },
            "evolution_oracle_samples": {
                "type": "integer",
                "description": "Top candidates to validate with AF2 in Stage 3 (default 20)",
            },
            "evolution_initial_samples": {
                "type": "integer",
                "description": "Initial K-means-selected AF2 training samples in round 1 (default 30)",
            },
            "evolution_rounds": {
                "type": "integer",
                "description": "Number of active-learning rounds (default 4)",
            },
            "evolution_samples_per_round": {
                "type": "integer",
                "description": "Legacy alias for per-round samples; evolution_oracle_samples controls Top-K (default 20)",
            },
            "evolution_label_source": {
                "type": "string",
                "enum": ["experimental", "in_silico_af2"],
                "default": "experimental",
                "description": "Label source for evolution mode. experimental recommends wet-lab candidates from recorded assay labels; in_silico_af2 preserves the legacy AF2-oracle loop.",
            },
            "evolution_objective_metric": {
                "type": "string",
                "default": "activity",
                "description": "Metric name to use as the supervised objective when evolution_label_source='experimental'.",
            },
            "evolution_experiment_source_run_id": {
                "type": "string",
                "description": "Optional previous run_id whose experiments.jsonl should be used as labels for experimental evolution.",
            },
            "evolution_surrogate_model": {
                "type": "string",
                "enum": ["rf", "ridge", "lightgbm", "xgboost", "ensemble"],
                "default": "rf",
                "description": "Surrogate model used by evolution mode. RF is the default pLDDT triage model; Ridge and ensemble are optional alternatives.",
            },
            "use_memory_bank": {
                "type": "boolean",
                "description": "Use memory-bank candidate routing in evolution mode",
            },
            "surrogate_triage_enabled": {
                "type": "boolean",
                "default": False,
                "description": "Use one-round surrogate triage in the standard pipeline to reduce AF2/ColabFold calls after SoluProt filtering.",
            },
            "surrogate_triage_scope": {
                "type": "string",
                "enum": ["per_tier", "pooled_tiers"],
                "default": "per_tier",
                "description": "Apply the AF2 triage budget separately within each conservation tier or once across the pooled multi-tier candidate set.",
            },
            "surrogate_triage_initial_samples": {
                "type": "integer",
                "default": 30,
                "description": "Number of diverse SoluProt-passed candidates to label with AF2 before fitting the surrogate triage model.",
            },
            "surrogate_triage_top_k": {
                "type": "integer",
                "default": 20,
                "description": "Number of surrogate-ranked candidates to send to AF2 after the initial labelled set.",
            },
            "surrogate_triage_model": {
                "oneOf": [
                    {
                        "type": "string",
                        "enum": ["auto", "rf", "ridge", "lightgbm", "xgboost", "ensemble"],
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["auto", "rf", "ridge", "lightgbm", "xgboost", "ensemble"],
                        },
                        "minItems": 1,
                    },
                ],
                "default": "auto",
                "description": "Top K selection method for one-round standard-pipeline AF2 triage. Auto selects the best model by internal CV on the AF2-labelled training set.",
            },
            "surrogate_triage_comparator_models": {
                "oneOf": [
                    {
                        "type": "string",
                        "enum": ["rf", "ridge", "lightgbm", "xgboost"],
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["rf", "ridge", "lightgbm", "xgboost"],
                        },
                    },
                ],
                "default": ["rf", "ridge", "lightgbm", "xgboost"],
                "description": "Models compared on the same AF2-labelled training set; the selected policy chooses final Top K candidates.",
            },
            "surrogate_triage_ensemble_models": {
                "oneOf": [
                    {
                        "type": "string",
                        "enum": ["rf", "ridge", "lightgbm", "xgboost"],
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["rf", "ridge", "lightgbm", "xgboost"],
                        },
                    },
                ],
                "default": [],
                "description": "Optional rank-mean ensemble members. Leave empty to skip ensemble comparison unless surrogate_triage_model is explicitly ensemble.",
            },
            "surrogate_triage_cv_folds": {
                "type": "integer",
                "default": 5,
                "description": "Internal cross-validation fold count used for auto surrogate acquisition selection.",
            },
            "agent_panel_enabled": {"type": "boolean"},
            "auto_recover": {"type": "boolean"},
            "wt_compare": {"type": "boolean"},
            "mask_consensus_apply": {"type": "boolean"},
            "auto_retry": {"type": "boolean"},
            "auto_retry_max": {"type": "integer"},
            "auto_retry_backoff_s": {"type": "number"},
        },
        "anyOf": [
            {"required": ["target_fasta"]},
            {"required": ["target_pdb"]},
            {"required": ["bioemu_sequence"]},
            {"required": ["rfd3_inputs"]},
            {"required": ["rfd3_inputs_text"]},
            {"required": ["rfd3_input_pdb"]},
            {"required": ["rfd3_input_files"]},
        ],
    }


def _extract_text_from_base64_pdf(b64_data: str) -> str:
    import base64
    import io

    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is required to process PDFs")

    try:
        pdf_bytes = base64.b64decode(b64_data)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"Failed to parse PDF: {str(e)}")


def _analyze_paper_for_masking(
    runner: PipelineRunner, arguments: dict[str, Any]
) -> dict[str, Any]:
    file_b64 = arguments.get("file_b64")
    file_text = arguments.get("file_text")
    target_sequence = str(arguments.get("target_sequence") or "").strip()

    if not file_b64 and not file_text:
        raise ValueError("Either file_b64 or file_text must be provided")

    paper_content = ""
    if file_b64:
        paper_content = _extract_text_from_base64_pdf(str(file_b64))
    else:
        paper_content = str(file_text).strip()

    if not paper_content:
        raise ValueError("Could not extract any text from the provided document")

    if not runner.gemini or not runner.gemini.is_available():
        raise RuntimeError("Gemini reasoning agent is not configured or unavailable")

    system_instruction = (
        "You are an expert structural biologist. Your task is to read a research paper and extract structural constraints for protein design. "
        "Identify critical residues that MUST NOT be mutated (e.g., catalytic triads, binding interfaces, conserved motifs). "
        "Return the result ONLY as a valid JSON object matching this schema:\n"
        "{\n"
        '  "suggested_masks": [\n'
        "    {\n"
        '      "chain": "A",\n'
        '      "residue_index": 64,\n'
        '      "residue_name": "HIS",\n'
        '      "label": "Short descriptive label (e.g., Catalytic Site)",\n'
        '      "evidence": "Exact quote from the paper justifying this selection",\n'
        '      "confidence": "high" or "low_sequence_mismatch" (use low if numbering seems to mismatch the provided sequence)\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    prompt = f"Reference Paper Content:\n{paper_content[:150000]}\n\n"  # Limit to avoid massive context
    if target_sequence:
        prompt += f"Target Protein Sequence (for numbering alignment check):\n{target_sequence}\n\n"
    prompt += "Extract the structural constraints as requested."

    try:
        import json
        try:
            import mlflow
        except ImportError:
            mlflow = None

        # Log to MLflow
        if mlflow is not None:
            try:
                mlflow.set_tracking_uri("http://127.0.0.1:18050")
                mlflow.set_experiment("PDF_Constraint_Extraction")
                with mlflow.start_run(run_name=f"PDF_Masking_{int(time.time())}"):
                    mlflow.log_param("has_target_sequence", bool(target_sequence))
                    mlflow.log_param("paper_text_length", len(paper_content))
                    mlflow.log_param("gemini_model", runner.gemini.model_name if hasattr(runner.gemini, "model_name") else "unknown")
            except Exception as mle:
                print(f"MLflow logging failed (non-critical): {mle}")

        response_text = runner.gemini.chat(system_instruction, prompt)

        # Clean up markdown code blocks if present
        clean_json = response_text
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()

        parsed = json.loads(clean_json)
        if "suggested_masks" not in parsed:
            parsed = {"suggested_masks": []}
        return {"success": True, "result": parsed}
    except Exception as e:
        raise RuntimeError(f"Agent failed to process the document: {str(e)}")


def sanitize_tool_for_strict_clients(tool: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``tool`` whose inputSchema is safe for strict
    function-calling clients (e.g. OpenAI strict mode, some MCP clients),
    which require the top-level schema to be ``type: object`` and reject
    top-level ``oneOf``/``anyOf``/``allOf``/``not``.

    Several tools express "provide one of these inputs" as a top-level
    ``anyOf: [{required: [...]}, ...]``. We drop that keyword and fold the
    requirement into the tool description so the constraint is still
    communicated (the tool functions validate the inputs at run time).
    """
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return tool
    new_schema = dict(schema)
    new_schema.setdefault("type", "object")
    notes: list[str] = []
    for key in ("anyOf", "oneOf"):
        clause = new_schema.pop(key, None)
        if isinstance(clause, list):
            fields: list[str] = []
            for sub in clause:
                if isinstance(sub, dict):
                    for field in sub.get("required") or []:
                        if field not in fields:
                            fields.append(field)
            if fields:
                verb = "exactly one" if key == "oneOf" else "at least one"
                notes.append(f"Provide {verb} of: {', '.join(fields)}.")
    new_schema.pop("allOf", None)
    new_schema.pop("not", None)
    out = dict(tool)
    out["inputSchema"] = new_schema
    if notes:
        desc = str(out.get("description") or "").rstrip()
        out["description"] = (desc + " " if desc else "") + " ".join(notes)
    return out


def tool_definitions() -> list[dict[str, Any]]:
    run_schema = _pipeline_run_schema()
    return [
        {
            "name": "pipeline.analyze_paper_for_masking",
            "description": "Analyze a research paper (PDF base64 or text) to extract residues that should be masked.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_b64": {"type": "string"},
                    "file_text": {"type": "string"},
                    "target_sequence": {"type": "string"},
                },
            },
        },
        {
            "name": "pipeline.run",
            "description": "Run the full protein design pipeline (MMseqs2→mask→ProteinMPNN→SoluProt→ColabFold/AF2→optional WT Diff).",
            "inputSchema": run_schema,
        },
        {
            "name": "pipeline.preflight",
            "description": "Validate inputs and configuration without running the pipeline.",
            "inputSchema": copy.deepcopy(run_schema),
        },
        {
            "name": "pipeline.classify_residues",
            "description": (
                "Classify residues as surface/core/interface (matches the web app's 3D picker) "
                "for region-based design selection."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_pdb": {
                        "type": "string",
                        "description": "Raw PDB file text (ATOM/HETATM records).",
                    },
                    "surface_area_cutoff": {
                        "type": "number",
                        "description": "Exposed-area threshold (Å²) above which a residue is surface. Default 2.5.",
                    },
                    "probe_radius": {
                        "type": "number",
                        "description": "Solvent probe radius (Å) for SASA. Default 1.4.",
                    },
                    "surface_max_neighbors": {
                        "type": "integer",
                        "description": "Fallback: residues with ≤ this many spatial neighbours → surface. Default 3.",
                    },
                    "core_min_neighbors": {
                        "type": "integer",
                        "description": "Fallback: residues with ≥ this many spatial neighbours → core. Default 8.",
                    },
                },
                "required": ["target_pdb"],
            },
        },
        {
            "name": "pipeline.af2_predict",
            "description": "Run ColabFold/AlphaFold2 on input FASTA/sequence (standalone).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_fasta": {"type": "string"},
                    "target_pdb": {"type": "string"},
                    "af2_model_preset": {"type": "string"},
                    "af2_db_preset": {"type": "string"},
                    "af2_max_template_date": {"type": "string"},
                    "af2_extra_flags": {"type": "string"},
                    "af2_provider": {"type": "string", "enum": ["colabfold", "af2"]},
                    "run_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "evolution_mode": {
                        "type": "boolean",
                        "description": "Run multi-round local active-learning evolution mode",
                    },
                    "evolution_pool_size": {
                        "type": "integer",
                        "description": "Initial sequences to generate in Stage 1 (default 1000)",
                    },
                    "evolution_oracle_samples": {
                        "type": "integer",
                        "description": "Top candidates to validate with AF2 in Stage 3 (default 20)",
                    },
                    "evolution_initial_samples": {
                        "type": "integer",
                        "description": "Initial K-means-selected AF2 training samples in round 1 (default 30)",
                    },
                    "evolution_rounds": {
                        "type": "integer",
                        "description": "Number of active-learning rounds (default 4)",
                    },
                    "evolution_samples_per_round": {
                        "type": "integer",
                        "description": "Legacy alias for per-round samples; evolution_oracle_samples controls Top-K (default 20)",
                    },
                },
                "anyOf": [{"required": ["target_fasta"]}, {"required": ["target_pdb"]}],
            },
        },
        {
            "name": "pipeline.diffdock",
            "description": "Run DiffDock on a protein PDB and ligand (standalone).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "protein_pdb": {"type": "string"},
                    "target_pdb": {"type": "string"},
                    "diffdock_ligand_smiles": {"type": "string"},
                    "diffdock_ligand_sdf": {"type": "string"},
                    "ligand_smiles": {"type": "string"},
                    "ligand_sdf": {"type": "string"},
                    "complex_name": {"type": "string"},
                    "diffdock_config": {"type": "string"},
                    "diffdock_extra_args": {"type": "string"},
                    "diffdock_cuda_visible_devices": {"type": "string"},
                    "run_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "evolution_mode": {
                        "type": "boolean",
                        "description": "Run multi-round local active-learning evolution mode",
                    },
                    "evolution_pool_size": {
                        "type": "integer",
                        "description": "Initial sequences to generate in Stage 1 (default 1000)",
                    },
                    "evolution_oracle_samples": {
                        "type": "integer",
                        "description": "Top candidates to validate with AF2 in Stage 3 (default 20)",
                    },
                    "evolution_initial_samples": {
                        "type": "integer",
                        "description": "Initial K-means-selected AF2 training samples in round 1 (default 30)",
                    },
                    "evolution_rounds": {
                        "type": "integer",
                        "description": "Number of active-learning rounds (default 4)",
                    },
                    "evolution_samples_per_round": {
                        "type": "integer",
                        "description": "Legacy alias for per-round samples; evolution_oracle_samples controls Top-K (default 20)",
                    },
                },
                "anyOf": [{"required": ["protein_pdb"]}, {"required": ["target_pdb"]}],
            },
        },
        {
            "name": "pipeline.submit_feedback",
            "description": "Submit a feedback rating (good/bad) for a run or artifact.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "rating": {"type": "string", "enum": ["good", "bad"]},
                    "reasons": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                        ]
                    },
                    "comment": {"type": "string"},
                    "artifact_path": {"type": "string"},
                    "stage": {"type": "string"},
                    "metrics": {"type": "object"},
                    "user": {"type": "object"},
                },
                "required": ["run_id", "rating"],
            },
        },
        {
            "name": "pipeline.list_feedback",
            "description": "List feedback entries for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.submit_experiment",
            "description": "Submit an experimental result for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "assay_type": {"type": "string"},
                    "result": {
                        "type": "string",
                        "enum": ["success", "fail", "inconclusive"],
                    },
                    "metrics": {"type": "object"},
                    "conditions": {"type": "string"},
                    "sample_id": {"type": "string"},
                    "candidate_id": {"type": "string"},
                    "sequence_id": {"type": "string"},
                    "metric_name": {"type": "string"},
                    "metric_value": {"type": "number"},
                    "metric_unit": {"type": "string"},
                    "metric_direction": {
                        "type": "string",
                        "enum": ["maximize", "minimize"],
                    },
                    "replicate_id": {"type": "string"},
                    "artifact_path": {"type": "string"},
                    "note": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["run_id", "result"],
            },
        },
        {
            "name": "pipeline.list_experiments",
            "description": "List experimental results for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.list_agent_events",
            "description": "List agent panel events for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.generate_report",
            "description": "Generate a markdown report for a run from artifacts, feedback, and experiments.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.save_report",
            "description": "Save a report revision for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "content": {"type": "string"},
                    "source": {"type": "string"},
                    "user": {"type": "object"},
                    "attachments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "text": {"type": "string"},
                                "base64": {"type": "string"},
                                "content_type": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                },
                "required": ["run_id", "content"],
            },
        },
        {
            "name": "pipeline.get_report",
            "description": "Get the latest report for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.compare_runs",
            "description": "Compare key run metrics against a baseline run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "baseline_run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.get_hit_list",
            "description": "Build and rank final candidates using weighted scoring.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "limit": {"type": "integer"},
                    "min_score": {"type": "number"},
                    "rmsd_ref": {"type": "number"},
                    "weights": {
                        "type": "object",
                        "properties": {
                            "soluprot": {"type": "number"},
                            "plddt": {"type": "number"},
                            "rmsd": {"type": "number"},
                            "novelty": {"type": "number"},
                        },
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.export_results_package",
            "description": "Create a zip package with reports, tables, JSON, and top PDB artifacts.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "include_top_n": {"type": "integer"},
                    "rmsd_ref": {"type": "number"},
                    "weights": {
                        "type": "object",
                        "properties": {
                            "soluprot": {"type": "number"},
                            "plddt": {"type": "number"},
                            "rmsd": {"type": "number"},
                            "novelty": {"type": "number"},
                        },
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.run_af2",
            "description": "Run ColabFold/AlphaFold2 on provided FASTA (no full pipeline).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fasta": {
                        "type": "string",
                        "description": "FASTA text (one or more sequences).",
                    },
                    "sequence": {
                        "type": "string",
                        "description": "Single sequence (non-FASTA).",
                    },
                    "sequence_id": {"type": "string"},
                    "af2_model_preset": {
                        "type": "string",
                        "description": "auto | monomer | multimer. Use 'multimer' for a complex, with the chains joined by '/' in the input sequence.",
                    },
                    "af2_db_preset": {"type": "string"},
                    "af2_max_template_date": {"type": "string"},
                    "af2_extra_flags": {"type": "string"},
                    "af2_provider": {
                        "type": "string",
                        "enum": ["colabfold", "af2"],
                        "description": "Multimer requires 'colabfold'; the stock AlphaFold2 worker rejects a multi-chain sequence here.",
                    },
                    "af2_chain_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Chain ids in the same order as the '/'-separated chains. The count must match, or the run is rejected.",
                    },
                    "run_id": {"type": "string"},
                    "force": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "evolution_mode": {
                        "type": "boolean",
                        "description": "Run multi-round local active-learning evolution mode",
                    },
                    "evolution_pool_size": {
                        "type": "integer",
                        "description": "Initial sequences to generate in Stage 1 (default 1000)",
                    },
                    "evolution_oracle_samples": {
                        "type": "integer",
                        "description": "Top candidates to validate with AF2 in Stage 3 (default 20)",
                    },
                    "evolution_initial_samples": {
                        "type": "integer",
                        "description": "Initial K-means-selected AF2 training samples in round 1 (default 30)",
                    },
                    "evolution_rounds": {
                        "type": "integer",
                        "description": "Number of active-learning rounds (default 4)",
                    },
                    "evolution_samples_per_round": {
                        "type": "integer",
                        "description": "Legacy alias for per-round samples; evolution_oracle_samples controls Top-K (default 20)",
                    },
                },
                "anyOf": [{"required": ["fasta"]}, {"required": ["sequence"]}],
            },
        },
        {
            "name": "pipeline.run_diffdock",
            "description": "Run DiffDock on a protein PDB + ligand (no full pipeline).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "protein_pdb": {"type": "string"},
                    "ligand_smiles": {"type": "string"},
                    "ligand_sdf": {"type": "string"},
                    "complex_name": {"type": "string"},
                    "diffdock_config": {"type": "string"},
                    "diffdock_extra_args": {"type": "string"},
                    "diffdock_cuda_visible_devices": {"type": "string"},
                    "run_id": {"type": "string"},
                    "force": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "evolution_mode": {
                        "type": "boolean",
                        "description": "Run multi-round local active-learning evolution mode",
                    },
                    "evolution_pool_size": {
                        "type": "integer",
                        "description": "Initial sequences to generate in Stage 1 (default 1000)",
                    },
                    "evolution_oracle_samples": {
                        "type": "integer",
                        "description": "Top candidates to validate with AF2 in Stage 3 (default 20)",
                    },
                    "evolution_initial_samples": {
                        "type": "integer",
                        "description": "Initial K-means-selected AF2 training samples in round 1 (default 30)",
                    },
                    "evolution_rounds": {
                        "type": "integer",
                        "description": "Number of active-learning rounds (default 4)",
                    },
                    "evolution_samples_per_round": {
                        "type": "integer",
                        "description": "Legacy alias for per-round samples; evolution_oracle_samples controls Top-K (default 20)",
                    },
                },
                "anyOf": [
                    {"required": ["protein_pdb", "ligand_smiles"]},
                    {"required": ["protein_pdb", "ligand_sdf"]},
                ],
            },
        },
        {
            "name": "pipeline.plan_from_prompt",
            "description": "Route a natural-language prompt and return missing inputs/questions without running.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "target_fasta": {"type": "string"},
                    "target_pdb": {"type": "string"},
                    "rfd3_input_pdb": {"type": "string"},
                    "rfd3_contig": {"type": "string"},
                    "diffdock_ligand_smiles": {"type": "string"},
                    "diffdock_ligand_sdf": {"type": "string"},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "pipeline.run_from_prompt",
            "description": "Route a natural-language prompt to a pipeline request and run it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "target_fasta": {"type": "string"},
                    "target_pdb": {"type": "string"},
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "agent_panel_enabled": {"type": "boolean"},
                    "auto_recover": {"type": "boolean"},
                    "auto_retry": {"type": "boolean"},
                    "auto_retry_max": {"type": "integer"},
                    "auto_retry_backoff_s": {"type": "number"},
                },
                "required": ["prompt"],
                "anyOf": [{"required": ["target_fasta"]}, {"required": ["target_pdb"]}],
            },
        },
        {
            "name": "pipeline.status",
            "description": "Get run status by run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.list_runs",
            "description": "List run_ids, excluding internal evolution/CATH child runs by default.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "query": {"type": "string"},
                    "include_subruns": {"type": "boolean"},
                    "include_cath": {"type": "boolean"},
                },
            },
        },
        {
            "name": "pipeline.save_project",
            "description": "Create or update a persisted project record for organizing rounds and runs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "description": {"type": "string"},
                    "target_summary": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "pipeline.list_projects",
            "description": "List visible persisted project records for the current user.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "include_archived": {"type": "boolean"},
                    "user": {"type": "object"},
                },
            },
        },
        {
            "name": "pipeline.get_project",
            "description": "Read a persisted project record by project_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id"],
            },
        },
        {
            "name": "pipeline.save_round",
            "description": "Create or update a persisted round record inside a project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "parent_round_id": {"type": "string"},
                    "title": {"type": "string"},
                    "goal": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "notes": {"type": "string"},
                    "next_round_notes": {"type": "string"},
                    "status": {"type": "string"},
                    "linked_run_ids": {"type": "array", "items": {"type": "string"}},
                    "selected_candidates": {},
                    "experiment_summary": {},
                    "user": {"type": "object"},
                },
                "required": ["project_id", "title"],
            },
        },
        {
            "name": "pipeline.list_rounds",
            "description": "List visible persisted round records, optionally scoped to a project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "limit": {"type": "integer"},
                    "include_archived": {"type": "boolean"},
                    "user": {"type": "object"},
                },
            },
        },
        {
            "name": "pipeline.get_round",
            "description": "Read a persisted round record by project_id and round_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id", "round_id"],
            },
        },
        {
            "name": "pipeline.archive_project",
            "description": "Archive a project record so it is hidden from default project lists without touching run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id"],
            },
        },
        {
            "name": "pipeline.delete_project",
            "description": "Delete a project record and, optionally, its round metadata without deleting run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "delete_rounds": {"type": "boolean"},
                    "user": {"type": "object"},
                },
                "required": ["project_id"],
            },
        },
        {
            "name": "pipeline.restore_project",
            "description": "Restore an archived project record back into default project lists without touching run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id"],
            },
        },
        {
            "name": "pipeline.archive_round",
            "description": "Archive a round record so it is hidden from default round lists without touching run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id", "round_id"],
            },
        },
        {
            "name": "pipeline.restore_round",
            "description": "Restore an archived round record back into default round lists without touching run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id", "round_id"],
            },
        },
        {
            "name": "pipeline.delete_round",
            "description": "Delete a round record without deleting any linked run outputs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "round_id": {"type": "string"},
                    "user": {"type": "object"},
                },
                "required": ["project_id", "round_id"],
            },
        },
        {
            "name": "pipeline.delete_run",
            "description": "Delete a run directory and all artifacts under run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "force": {"type": "boolean"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.cancel_run",
            "description": "Cancel in-flight RunPod jobs for a run_id and mark the run as cancelled.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.list_artifacts",
            "description": "List artifact paths under a run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "prefix": {"type": "string"},
                    "max_depth": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.read_artifact",
            "description": "Read an artifact file under a run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "encoding": {"type": "string"},
                    "base64": {"type": "boolean"},
                },
                "required": ["run_id", "path"],
            },
        },
        {
            "name": "pipeline.save_workflow_session",
            "description": "Save Workflow Studio session metadata under a run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "session": {"type": "object"},
                },
                "required": ["run_id", "session"],
            },
        },
        {
            "name": "pipeline.get_workflow_session",
            "description": "Read Workflow Studio session metadata for a run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "pipeline.cath_get_batch_overview",
            "description": "Summarize CATH batch progress across train/val/test subsets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "item_limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "pipeline.cath_launch_batch",
            "description": "Launch a managed CATH batch generation job for one subset.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "subset": {"type": "string", "enum": ["train", "val", "test"]},
                    "keep_local": {"type": "boolean"},
                    "max_workers": {"type": "integer"},
                },
                "required": ["subset"],
            },
        },
        {
            "name": "pipeline.cath_launch_training",
            "description": "Launch surrogate training from local CATH outputs for selected subsets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "subsets": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["train", "val", "test"]},
                    },
                },
                "required": ["subsets"],
            },
        },
        {
            "name": "pipeline.cath_list_jobs",
            "description": "List managed CATH batch/training jobs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["batch", "train", "cath_batch", "cath_train"],
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "pipeline.cath_get_job",
            "description": "Read metadata for one managed CATH job.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "pipeline.cath_read_job_log",
            "description": "Read the tail of a managed CATH job log.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "max_bytes": {"type": "integer"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "pipeline.cath_stop_job",
            "description": "Request stop for a managed CATH batch/training job.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "pipeline.cath_delete_job",
            "description": "Delete a managed job record from the operations history.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "pipeline.runpod_list_endpoints",
            "description": "List RunPod serverless endpoints and highlight the ones used by protein_pipeline.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "managed_only": {"type": "boolean"},
                    "include_workers": {"type": "boolean"},
                },
            },
        },
        {
            "name": "pipeline.runpod_get_endpoint",
            "description": "Get a RunPod serverless endpoint with worker details.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint_id": {"type": "string"},
                    "include_workers": {"type": "boolean"},
                },
                "required": ["endpoint_id"],
            },
        },
        {
            "name": "pipeline.runpod_update_endpoint",
            "description": "Patch a RunPod serverless endpoint configuration such as GPU types or worker scaling limits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint_id": {"type": "string"},
                    "patch": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "gpuTypeIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "dataCenterIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "idleTimeout": {"type": "integer"},
                            "executionTimeoutMs": {"type": "integer"},
                            "flashBoot": {"type": "boolean"},
                            "scalerType": {"type": "string"},
                            "scalerValue": {"type": "integer"},
                            "templateId": {"type": "string"},
                            "networkVolumeId": {"type": "string"},
                            "workersMin": {"type": "integer"},
                            "workersMax": {"type": "integer"},
                        },
                    },
                },
                "required": ["endpoint_id", "patch"],
            },
        },
        {
            "name": "pipeline.runpod_list_billing",
            "description": "Fetch recent RunPod serverless billing records for endpoint-level spend tracking.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint_id": {"type": "string"},
                    "days": {"type": "integer"},
                    "bucket_size": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                },
            },
        },
        {
            "name": "pipeline.runpod_get_history",
            "description": "Read server-collected RunPod usage and billing history stored for the admin dashboard.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint_id": {"type": "string"},
                    "days": {"type": "integer"},
                    "usage_resolution": {"type": "string"},
                    "billing_resolution": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "pipeline.model_provider_list",
            "description": "List configured model providers across RunPod and HTTP API backends.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_health": {"type": "boolean"},
                },
            },
        },
        {
            "name": "pipeline.model_provider_update",
            "description": "Create or update a model provider entry.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_key": {"type": "string"},
                    "provider": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "custom": {"type": "boolean"},
                            "provider_type": {"type": "string", "enum": ["runpod", "http_api", "disabled"]},
                            "endpoint_id": {"type": "string"},
                            "base_url": {"type": "string"},
                            "token": {"type": "string"},
                            "timeout_s": {"type": "number"},
                            "enabled": {"type": "boolean"},
                        },
                    },
                },
                "required": ["model_key", "provider"],
            },
        },
        {
            "name": "pipeline.model_provider_health",
            "description": "Run a health check against a model provider.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_key": {"type": "string"},
                    "provider": {
                        "type": "object",
                        "description": "Optional unsaved provider draft to check instead of the saved registry value.",
                    },
                },
                "required": ["model_key"],
            },
        },
        {
            "name": "pipeline.agent_chat",
            "description": "Reasoning agent that analyzes run status and expert insights to answer user questions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "lang": {"type": "string", "description": "Language code (en, ko)"},
                },
                "required": ["run_id", "prompt"],
            },
        },
        {
            "name": "pipeline.queue_eta",
            "description": "Approximate worker-queue ETA (jobs ahead, estimated wait/finish) for a run's remaining pipeline stages. Times are approximate; missing data degrades to counts only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Optional run id; if omitted, reports all RunPod-backed stages."},
                },
            },
        },
        {
            "name": "chat.list_models",
            "description": "List chat-capable models for an LLM provider. Commercial providers use a user-supplied API key (used only for this request, never stored); the local 'exaone' provider needs no key.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "description": "anthropic | openai | gemini | exaone (local, no key)"},
                    "api_key": {"type": "string", "description": "provider API key (browser-held); omit/empty for exaone"},
                },
                "required": ["provider"],
            },
        },
        {
            "name": "chat.send",
            "description": "Run one chatbot turn: the assistant may read run state and return a navigate action. Commercial providers use a user-supplied API key (browser-held, per-request only); the local 'exaone' provider needs no key.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "description": "anthropic | openai | gemini | exaone (local, no key)"},
                    "model": {"type": "string", "description": "model id; optional for exaone (uses the served default)"},
                    "api_key": {"type": "string", "description": "provider API key; omit/empty for exaone"},
                    "messages": {"type": "array", "description": "neutral chat history [{role,content}]"},
                    "context": {"type": "object", "description": "UI context {tab, run_id}"},
                    "attachments": {"type": "array", "description": "[{name, base64}] attached files"},
                    "session_id": {"type": "string", "description": "browser chat session id"},
                },
                "required": ["provider", "messages"],
            },
        },
        {
            "name": "chat.list_attachments",
            "description": "List files the user previously attached in this chat session (saved on the server).",
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    ]


@dataclass(frozen=True)
class ToolDispatcher:
    runner: PipelineRunner

    def list_tools(self) -> dict[str, Any]:
        return {"tools": [sanitize_tool_for_strict_clients(t) for t in tool_definitions()]}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "pipeline.analyze_paper_for_masking":
            return _analyze_paper_for_masking(self.runner, arguments)

        if name == "pipeline.agent_chat":
            return _agent_chat_tool(self.runner, arguments)

        if name == "pipeline.run":
            run_id = arguments.get("run_id")
            req = pipeline_request_from_args(arguments)
            _require_request_metadata_access(
                self.runner,
                project_id=req.project_id,
                round_id=req.round_id,
                user=arguments.get("user"),
            )
            retry = _auto_retry_config(arguments)
            normalized_run_id = (
                normalize_run_id(str(run_id)) if run_id is not None else None
            )
            if normalized_run_id is not None:
                status = load_status(self.runner.output_root, normalized_run_id)
                if isinstance(status, dict):
                    prior_state = str(status.get("state") or "").lower()
                    if prior_state == "running":
                        raise ValueError(
                            f"run_id={normalized_run_id} is already running; use pipeline.status or pipeline.cancel_run first"
                        )
                    if (
                        prior_state == "completed"
                        and not getattr(req, "force", False)
                        and not getattr(req, "start_from", None)
                    ):
                        raise ValueError(
                            f"run_id={normalized_run_id} already completed; "
                            f"set force=true to overwrite, use start_from=<stage> for partial rerun, "
                            f"or use a new run_id"
                        )
            if retry.enabled and normalized_run_id is None:
                normalized_run_id = new_run_id("pipeline")
            res = _run_with_auto_retry(
                self.runner, req, run_id=normalized_run_id, retry=retry
            )
            return {
                "run_id": res.run_id,
                "output_dir": res.output_dir,
                "summary": asdict(res),
            }

        if name == "pipeline.preflight":
            req = pipeline_request_from_args(arguments, strict_target=False)
            _require_request_metadata_access(
                self.runner,
                project_id=req.project_id,
                round_id=req.round_id,
                user=arguments.get("user"),
            )
            return preflight_request(
                req, self.runner, run_id=str(arguments.get("run_id") or "") or None
            )

        if name == "pipeline.classify_residues":
            target_pdb = _as_text(arguments.get("target_pdb"))
            if not target_pdb.strip():
                raise ValueError("target_pdb is required")
            kwargs: dict[str, Any] = {}
            if arguments.get("surface_area_cutoff") is not None:
                kwargs["surface_area_cutoff"] = float(arguments["surface_area_cutoff"])
            if arguments.get("probe_radius") is not None:
                kwargs["probe_radius"] = float(arguments["probe_radius"])
            if arguments.get("surface_max_neighbors") is not None:
                kwargs["surface_max_neighbors"] = int(arguments["surface_max_neighbors"])
            if arguments.get("core_min_neighbors") is not None:
                kwargs["core_min_neighbors"] = int(arguments["core_min_neighbors"])
            return _classify_residues(target_pdb, **kwargs)

        if name == "pipeline.af2_predict":
            return _run_af2_predict(self.runner, arguments)

        if name == "pipeline.diffdock":
            return _run_diffdock(self.runner, arguments)

        if name == "pipeline.run_af2":
            legacy_args = dict(arguments)
            if "target_fasta" not in legacy_args and legacy_args.get("fasta"):
                legacy_args["target_fasta"] = legacy_args.get("fasta")
            if "target_fasta" not in legacy_args and legacy_args.get("sequence"):
                sequence = _as_text(legacy_args.get("sequence")).strip()
                if sequence:
                    seq_id = (
                        str(legacy_args.get("sequence_id") or "seq1").strip() or "seq1"
                    )
                    legacy_args["target_fasta"] = f">{seq_id}\n{sequence}\n"
            return _run_af2_predict(self.runner, legacy_args)

        if name == "pipeline.run_diffdock":
            return _run_diffdock(self.runner, arguments)

        if name == "pipeline.submit_feedback":
            return _submit_feedback(self.runner, arguments)

        if name == "pipeline.list_feedback":
            return _list_feedback(self.runner, arguments)

        if name == "pipeline.submit_experiment":
            return _submit_experiment(self.runner, arguments)

        if name == "pipeline.list_experiments":
            return _list_experiments(self.runner, arguments)

        if name == "pipeline.list_agent_events":
            return _list_agent_events(self.runner, arguments)

        if name == "pipeline.generate_report":
            return _generate_report(self.runner, arguments)

        if name == "pipeline.save_report":
            return _save_report(self.runner, arguments)

        if name == "pipeline.get_report":
            return _get_report(self.runner, arguments)

        if name == "pipeline.compare_runs":
            return _compare_runs(self.runner, arguments)

        if name == "pipeline.get_hit_list":
            return _get_hit_list(self.runner, arguments)

        if name == "pipeline.export_results_package":
            return _export_results_package(self.runner, arguments)

        if name == "pipeline.plan_from_prompt":
            prompt = str(arguments.get("prompt") or "")
            target_fasta = _as_text(arguments.get("target_fasta"))
            target_pdb = _as_text(arguments.get("target_pdb"))
            rfd3_input_pdb = _as_text(arguments.get("rfd3_input_pdb"))
            rfd3_contig = str(arguments.get("rfd3_contig") or "").strip() or None
            diffdock_ligand_smiles = _as_text(arguments.get("diffdock_ligand_smiles"))
            diffdock_ligand_sdf = _as_text(arguments.get("diffdock_ligand_sdf"))
            return plan_from_prompt(
                prompt=prompt,
                target_fasta=target_fasta,
                target_pdb=target_pdb,
                rfd3_input_pdb=rfd3_input_pdb,
                rfd3_contig=rfd3_contig,
                diffdock_ligand_smiles=diffdock_ligand_smiles,
                diffdock_ligand_sdf=diffdock_ligand_sdf,
            )

        if name == "pipeline.run_from_prompt":
            run_id = arguments.get("run_id")
            retry = _auto_retry_config(arguments)
            prompt = str(arguments.get("prompt") or "")
            target_fasta = _as_text(arguments.get("target_fasta"))
            target_pdb = _as_text(arguments.get("target_pdb"))
            if not target_fasta.strip() and not target_pdb.strip():
                raise ValueError("One of target_fasta or target_pdb is required")
            req = request_from_prompt(
                prompt=prompt, target_fasta=target_fasta, target_pdb=target_pdb
            )
            agent_panel_enabled = _as_bool(
                arguments.get("agent_panel_enabled"), req.agent_panel_enabled
            )
            auto_recover = _as_bool(arguments.get("auto_recover"), req.auto_recover)
            project_id = _as_text(arguments.get("project_id")).strip() or None
            round_id = _as_text(arguments.get("round_id")).strip() or None
            req = replace(
                req,
                agent_panel_enabled=agent_panel_enabled,
                auto_recover=auto_recover,
                project_id=project_id,
                round_id=round_id,
            )
            _require_request_metadata_access(
                self.runner,
                project_id=req.project_id,
                round_id=req.round_id,
                user=arguments.get("user"),
            )
            normalized_run_id = (
                normalize_run_id(str(run_id)) if run_id is not None else None
            )
            if normalized_run_id is not None:
                status = load_status(self.runner.output_root, normalized_run_id)
                if isinstance(status, dict):
                    prior_state = str(status.get("state") or "").lower()
                    if prior_state == "running":
                        raise ValueError(
                            f"run_id={normalized_run_id} is already running; use pipeline.status or pipeline.cancel_run first"
                        )
                    if (
                        prior_state == "completed"
                        and not getattr(req, "force", False)
                        and not getattr(req, "start_from", None)
                    ):
                        raise ValueError(
                            f"run_id={normalized_run_id} already completed; "
                            f"set force=true to overwrite, use start_from=<stage> for partial rerun, "
                            f"or use a new run_id"
                        )
            if retry.enabled and normalized_run_id is None:
                normalized_run_id = new_run_id("pipeline")
            res = _run_with_auto_retry(
                self.runner, req, run_id=normalized_run_id, retry=retry
            )
            return {
                "routed_request": asdict(req),
                "run_id": res.run_id,
                "output_dir": res.output_dir,
                "summary": asdict(res),
            }

        if name == "pipeline.status":
            run_id = str(arguments.get("run_id") or "")
            if not run_id:
                raise ValueError("run_id is required")
            status = load_status(self.runner.output_root, run_id)
            if status is None:
                return {"run_id": run_id, "found": False}
            return {"run_id": run_id, "found": True, "status": status}

        if name == "pipeline.list_runs":
            limit = arguments.get("limit")
            return {
                "runs": list_runs(
                    self.runner.output_root,
                    limit=int(limit) if limit is not None else 50,
                    query=_as_text(arguments.get("query")).strip() or None,
                    include_subruns=_as_bool(arguments.get("include_subruns"), False),
                    include_cath=_as_bool(arguments.get("include_cath"), False),
                )
            }

        if name == "pipeline.save_project":
            return _save_project(self.runner, arguments)

        if name == "pipeline.list_projects":
            return _list_projects(self.runner, arguments)

        if name == "pipeline.get_project":
            return _get_project(self.runner, arguments)

        if name == "pipeline.save_round":
            return _save_round(self.runner, arguments)

        if name == "pipeline.list_rounds":
            return _list_rounds(self.runner, arguments)

        if name == "pipeline.get_round":
            return _get_round(self.runner, arguments)

        if name == "pipeline.archive_project":
            return _archive_project(self.runner, arguments)

        if name == "pipeline.restore_project":
            return _restore_project(self.runner, arguments)

        if name == "pipeline.delete_project":
            return _delete_project_record(self.runner, arguments)

        if name == "pipeline.archive_round":
            return _archive_round(self.runner, arguments)

        if name == "pipeline.restore_round":
            return _restore_round(self.runner, arguments)

        if name == "pipeline.delete_round":
            return _delete_round_record(self.runner, arguments)

        if name == "pipeline.delete_run":
            return _delete_run_tool(self.runner, arguments)

        if name == "pipeline.cancel_run":
            return _cancel_run_tool(self.runner, arguments)

        if name == "pipeline.list_artifacts":
            run_id = str(arguments.get("run_id") or "")
            if not run_id:
                raise ValueError("run_id is required")
            prefix = arguments.get("prefix")
            max_depth = _as_int(arguments.get("max_depth"), 4)
            limit = _as_int(arguments.get("limit"), 200)
            artifacts = list_artifacts(
                self.runner.output_root,
                run_id,
                prefix=str(prefix) if prefix is not None else None,
                max_depth=max_depth,
                limit=limit,
            )
            return {"run_id": run_id, "artifacts": artifacts}

        if name == "pipeline.read_artifact":
            run_id = str(arguments.get("run_id") or "")
            path = str(arguments.get("path") or "")
            if not run_id:
                raise ValueError("run_id is required")
            if not path:
                raise ValueError("path is required")
            max_bytes = _as_int(arguments.get("max_bytes"), 2_000_000)
            offset = _as_int(arguments.get("offset"), 0)
            encoding = str(arguments.get("encoding") or "utf-8")
            as_base64 = _as_bool(arguments.get("base64"), False)
            data, meta = read_artifact(
                self.runner.output_root,
                run_id,
                path=path,
                max_bytes=max_bytes,
                offset=offset,
            )
            if as_base64:
                meta["base64"] = base64.b64encode(data).decode("ascii")
                return {"run_id": run_id, **meta}
            meta["encoding"] = encoding
            meta["text"] = data.decode(encoding, errors="replace")
            return {"run_id": run_id, **meta}

        if name == "pipeline.save_workflow_session":
            run_id = str(arguments.get("run_id") or "")
            session = arguments.get("session")
            if not run_id:
                raise ValueError("run_id is required")
            if not isinstance(session, dict):
                raise ValueError("session must be an object")
            meta = save_workflow_session(self.runner.output_root, run_id, session)
            return {"run_id": run_id, "saved": True, **meta}

        if name == "pipeline.get_workflow_session":
            run_id = str(arguments.get("run_id") or "")
            if not run_id:
                raise ValueError("run_id is required")
            session = load_workflow_session(self.runner.output_root, run_id)
            return {
                "run_id": run_id,
                "found": isinstance(session, dict),
                "session": session if isinstance(session, dict) else None,
            }

        if name == "pipeline.cath_get_batch_overview":
            return _cath_get_batch_overview_tool(self.runner, arguments)

        if name == "pipeline.cath_launch_batch":
            return _cath_launch_batch_tool(self.runner, arguments)

        if name == "pipeline.cath_launch_training":
            return _cath_launch_training_tool(self.runner, arguments)

        if name == "pipeline.cath_list_jobs":
            return _cath_list_jobs_tool(self.runner, arguments)

        if name == "pipeline.cath_get_job":
            return _cath_get_job_tool(self.runner, arguments)

        if name == "pipeline.cath_read_job_log":
            return _cath_read_job_log_tool(self.runner, arguments)

        if name == "pipeline.cath_stop_job":
            return _cath_stop_job_tool(self.runner, arguments)

        if name == "pipeline.cath_delete_job":
            return _cath_delete_job_tool(self.runner, arguments)

        if name == "pipeline.runpod_list_endpoints":
            return _runpod_list_endpoints_tool(self.runner, arguments)

        if name == "pipeline.runpod_get_endpoint":
            return _runpod_get_endpoint_tool(self.runner, arguments)

        if name == "pipeline.runpod_update_endpoint":
            return _runpod_update_endpoint_tool(self.runner, arguments)

        if name == "pipeline.runpod_list_billing":
            return _runpod_list_billing_tool(self.runner, arguments)
        if name == "pipeline.runpod_get_history":
            return _runpod_get_history_tool(self.runner, arguments)

        if name == "pipeline.model_provider_list":
            return _model_provider_list_tool(self.runner, arguments)

        if name == "pipeline.model_provider_update":
            return _model_provider_update_tool(self.runner, arguments)

        if name == "pipeline.model_provider_health":
            return _model_provider_health_tool(self.runner, arguments)

        if name == "pipeline.queue_eta":
            return _queue_eta_tool(self.runner, arguments)

        if name == "chat.list_models":
            return _chat_list_models_tool(self.runner, arguments)

        if name == "chat.send":
            return _chat_send_tool(self.runner, arguments)

        if name == "chat.list_attachments":
            return _chat_list_attachments_tool(self.runner, arguments)

        raise ValueError(f"Unknown tool: {name}")
