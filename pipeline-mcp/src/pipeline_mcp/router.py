from __future__ import annotations

import json
import re

from .models import PipelineRequest


_STOP_WORDS = ("까지만", "stop", "중단", "까지만 실행")
_CONTIG_RE = re.compile(r"(?:rfd3_)?contig\s*[:=]?\s*([A-Za-z]\s*:?[0-9]+\s*-\s*[0-9]+)")
_KV_LINE_RE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*[:=]\s*(.+?)\s*$")
_KV_INLINE_RE = re.compile(r"(?<!\w)([A-Za-z_][\w-]*)\s*[:=]\s*([^\s,;]+)")
_FLAG_RE = re.compile(r"(?:^|\s)--([A-Za-z_][\w-]*)(?:[= ]([^\s]+))?")


_PROMPT_KEY_ALIASES = {
    "plddt": "af2_plddt_cutoff",
    "rmsd": "af2_rmsd_cutoff",
    "topk": "af2_top_k",
    "top_k": "af2_top_k",
    "af2_n": "af2_max_candidates_per_tier",
    "af2_per_tier": "af2_max_candidates_per_tier",
    "surrogate": "surrogate_triage_enabled",
    "surrogate_triage": "surrogate_triage_enabled",
    "surrogate_initial": "surrogate_triage_initial_samples",
    "surrogate_train": "surrogate_triage_initial_samples",
    "surrogate_topk": "surrogate_triage_top_k",
    "surrogate_top_k": "surrogate_triage_top_k",
    "surrogate_model": "surrogate_triage_model",
    "fold_provider": "af2_provider",
    "structure_provider": "af2_provider",
    "temp": "sampling_temp",
    "temperature": "sampling_temp",
    "mask_consensus": "mask_consensus_apply",
    "surface": "surface_only",
    "surface_only": "surface_only",
    "wt_diff_enabled": "novelty_enabled",
    "wtdiff_enabled": "novelty_enabled",
    "wt_diff_target_db": "novelty_target_db",
    "wtdiff_target_db": "novelty_target_db",
}

_PROMPT_BOOL_KEYS = {
    "dry_run",
    "force",
    "agent_panel_enabled",
    "auto_recover",
    "wt_compare",
    "mask_consensus_apply",
    "pdb_strip_nonpositive_resseq",
    "pdb_renumber_resseq_from_1",
    "mmseqs_use_gpu",
    "rfd3_use_ensemble",
    "rfd3_fail_on_duplicate_backbones",
    "surface_only",
    "bioemu_use",
    "ligand_mask_use_original_target",
    "surrogate_triage_enabled",
}
_PROMPT_INT_KEYS = {
    "num_seq_per_tier",
    "batch_size",
    "seed",
    "af2_top_k",
    "af2_max_candidates_per_tier",
    "mmseqs_max_seqs",
    "mmseqs_threads",
    "rfd3_design_index",
    "rfd3_max_return_designs",
    "bioemu_num_samples",
    "bioemu_batch_size_100",
    "bioemu_base_seed",
    "bioemu_max_return_structures",
    "bioemu_max_attempted_structures",
    "surrogate_triage_initial_samples",
    "surrogate_triage_top_k",
    "conservation_cluster_cov_mode",
    "conservation_cluster_kmer_per_seq",
}
_PROMPT_FLOAT_KEYS = {
    "sampling_temp",
    "soluprot_cutoff",
    "af2_plddt_cutoff",
    "af2_rmsd_cutoff",
    "ligand_mask_distance",
    "rfd3_partial_t",
    "rfd3_target_rmsd_cutoff",
    "msa_min_coverage",
    "msa_min_identity",
    "query_pdb_min_identity",
    "conservation_cluster_min_seq_id",
    "conservation_cluster_coverage",
    "surface_min_rel",
    "surface_min_abs",
    "bioemu_target_rmsd_cutoff",
    "pi_min",
    "pi_max",
}
_PROMPT_LIST_STR_KEYS = {
    "design_chains",
    "ligand_resnames",
    "ligand_atom_chains",
    "af2_sequence_ids",
    "rfd3_ligand",
}
_PROMPT_LIST_FLOAT_KEYS = {"conservation_tiers"}
_PROMPT_DICT_KEYS = {
    "fixed_positions_extra",
    "rfd3_env",
    "rfd3_inputs",
    "rfd3_input_files",
    "bioemu_env",
}

_PROMPT_ALLOWED_KEYS = (
    _PROMPT_BOOL_KEYS
    | _PROMPT_INT_KEYS
    | _PROMPT_FLOAT_KEYS
    | _PROMPT_LIST_STR_KEYS
    | _PROMPT_LIST_FLOAT_KEYS
    | _PROMPT_DICT_KEYS
    | {
        "stop_after",
        "start_from",
        "rfd3_contig",
        "rfd3_input_pdb",
        "rfd3_spec_name",
        "rfd3_sampling_strategy",
        "rfd3_select_unfixed_sequence",
        "rfd3_cli_args",
        "bioemu_sequence",
        "bioemu_model_name",
        "diffdock_ligand_smiles",
        "diffdock_ligand_sdf",
        "diffdock_config",
        "diffdock_extra_args",
        "diffdock_cuda_visible_devices",
        "conservation_mode",
        "conservation_weighting",
        "conservation_cluster_method",
        "af2_model_preset",
        "af2_db_preset",
        "af2_max_template_date",
        "af2_extra_flags",
        "af2_provider",
        "surrogate_triage_model",
        "mmseqs_target_db",
        "novelty_target_db",
        "query_pdb_policy",
    }
)


def _normalize_prompt_key(raw: str) -> str:
    key = str(raw or "").strip().lower().replace("-", "_")
    return _PROMPT_KEY_ALIASES.get(key, key)


def _split_prompt_list(raw: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    return parts


def _parse_prompt_bool(raw: str) -> bool | None:
    v = str(raw or "").strip().lower()
    if v in {"1", "true", "yes", "y", "on", "enable", "enabled", "apply", "use"}:
        return True
    if v in {"0", "false", "no", "n", "off", "disable", "disabled", "skip"}:
        return False
    return None


def _parse_fixed_positions_extra(raw: str) -> dict[str, list[int]] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            positions = [int(float(p)) for p in parsed if str(p).strip()]
            positions = [p for p in positions if p > 0]
            return {"*": positions} if positions else None
        return None
    if ":" in text:
        out: dict[str, list[int]] = {}
        for segment in re.split(r"[;\n]+", text):
            if ":" not in segment:
                continue
            chain, positions_raw = segment.split(":", 1)
            chain = chain.strip()
            if not chain:
                continue
            positions = [p for p in _split_prompt_list(positions_raw) if p]
            ints = [int(float(p)) for p in positions if p.replace(".", "", 1).isdigit()]
            ints = [p for p in ints if p > 0]
            if ints:
                out[chain] = ints
        return out or None
    positions = [p for p in _split_prompt_list(text) if p]
    ints = [int(float(p)) for p in positions if p.replace(".", "", 1).isdigit()]
    ints = [p for p in ints if p > 0]
    return {"*": ints} if ints else None


def _coerce_prompt_value(key: str, raw: str) -> object:
    value = str(raw or "").strip()
    if key in _PROMPT_BOOL_KEYS:
        parsed = _parse_prompt_bool(value)
        if parsed is None:
            raise ValueError(f"invalid boolean for {key}")
        return parsed
    if key in _PROMPT_INT_KEYS:
        return int(float(value))
    if key in _PROMPT_FLOAT_KEYS:
        return float(value)
    if key in _PROMPT_LIST_FLOAT_KEYS:
        if value.startswith("["):
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        return [float(v) for v in _split_prompt_list(value)]
    if key in _PROMPT_LIST_STR_KEYS:
        if value.startswith("["):
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None]
        return _split_prompt_list(value)
    if key in _PROMPT_DICT_KEYS:
        if key == "fixed_positions_extra":
            parsed = _parse_fixed_positions_extra(value)
            if parsed is None:
                raise ValueError("fixed_positions_extra expects JSON or chain:number list")
            return parsed
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"{key} must be a JSON object")
        return parsed
    return value


def _extract_prompt_overrides(raw_prompt: str) -> tuple[dict[str, object], list[str]]:
    overrides: dict[str, object] = {}
    errors: list[str] = []

    for m in _KV_INLINE_RE.finditer(raw_prompt):
        key = _normalize_prompt_key(m.group(1))
        if key not in _PROMPT_ALLOWED_KEYS:
            continue
        try:
            overrides[key] = _coerce_prompt_value(key, m.group(2))
        except Exception as exc:
            errors.append(str(exc))

    for m in _FLAG_RE.finditer(raw_prompt):
        key = _normalize_prompt_key(m.group(1))
        if key not in _PROMPT_ALLOWED_KEYS:
            continue
        if m.group(2) is None:
            if key in _PROMPT_BOOL_KEYS:
                overrides[key] = True
            continue
        try:
            overrides[key] = _coerce_prompt_value(key, m.group(2))
        except Exception as exc:
            errors.append(str(exc))

    for line in raw_prompt.splitlines():
        m = _KV_LINE_RE.match(line)
        if not m:
            continue
        key = _normalize_prompt_key(m.group(1))
        if key not in _PROMPT_ALLOWED_KEYS:
            continue
        try:
            overrides[key] = _coerce_prompt_value(key, m.group(2))
        except Exception as exc:
            errors.append(str(exc))

    return overrides, errors


def route_prompt_with_errors(prompt: str) -> tuple[dict[str, object], list[str]]:
    raw_prompt = str(prompt or "")
    p = raw_prompt.strip().lower()
    stop_after: str | None = None
    if any(w in p for w in _STOP_WORDS):
        if "msa" in p or "mmseqs" in p:
            stop_after = "msa"
        elif "rfd3" in p or "rfdiff" in p or "diffusion" in p:
            stop_after = "rfd3"
        elif "bioemu" in p:
            stop_after = "bioemu"
        elif "design" in p or "proteinmpnn" in p:
            stop_after = "design"
        elif "soluprot" in p:
            stop_after = "soluprot"
        elif "af2" in p or "alphafold" in p or "colabfold" in p:
            stop_after = "af2"
        elif "novel" in p or "search" in p or "검색" in p or "wt diff" in p or "wt_diff" in p or "wt 차이" in p:
            stop_after = "novelty"

    tiers = None
    if "30" in p and "50" in p and "70" in p:
        tiers = [0.3, 0.5, 0.7]

    num = None
    m = re.search(r"(\d+)\s*(개|seq|sequences?)", p)
    if m:
        try:
            num = int(m.group(1))
        except Exception:
            num = None

    partial_t = None
    m = re.search(r"(?:diffuser\.partial[_\s-]*t|partial[_\s-]*t)\s*[:=]?\s*(\d+)", p)
    if m:
        try:
            partial_t = int(m.group(1))
        except Exception:
            partial_t = None

    out: dict[str, object] = {}
    if partial_t is not None:
        out["rfd3_partial_t"] = partial_t
    if stop_after:
        out["stop_after"] = stop_after
    if tiers:
        out["conservation_tiers"] = tiers
    if num is not None:
        out["num_seq_per_tier"] = num

    if "mask_consensus_apply" not in out:
        if "mask consensus" in p or "합의 마스킹" in raw_prompt:
            if re.search(r"\b(no|off|disable|disabled)\b", p) or any(
                word in raw_prompt for word in ("끄기", "사용 안", "적용 안")
            ):
                out["mask_consensus_apply"] = False
            else:
                out["mask_consensus_apply"] = True

    if "surface_only" not in out:
        if "surface" in p or "표면" in raw_prompt:
            if re.search(r"\b(no|off|disable|disabled)\b", p) or any(
                word in raw_prompt for word in ("끄기", "사용 안", "적용 안")
            ):
                out["surface_only"] = False
            else:
                out["surface_only"] = True

    if "soluprot_cutoff" not in out and "soluprot" in p:
        m = re.search(r"soluprot[^0-9]*([0-9]+(?:\.[0-9]+)?)", p)
        if m:
            try:
                out["soluprot_cutoff"] = float(m.group(1))
            except Exception:
                pass

    if "af2_plddt_cutoff" not in out:
        m = re.search(r"plddt[^0-9]*([0-9]+(?:\.[0-9]+)?)", p)
        if m:
            try:
                out["af2_plddt_cutoff"] = float(m.group(1))
            except Exception:
                pass

    if "af2_rmsd_cutoff" not in out:
        m = re.search(r"rmsd[^0-9]*([0-9]+(?:\.[0-9]+)?)", p)
        if m:
            try:
                out["af2_rmsd_cutoff"] = float(m.group(1))
            except Exception:
                pass

    if ("pi_min" not in out) and ("pi_max" not in out):
        if re.search(r"\bp\s*I\b|\bpi\b", raw_prompt, re.IGNORECASE):
            m = re.search(r"p\s*I\s*(<=|=<|>=|=>|<|>|≤|≥)?\s*([0-9]+(?:\.[0-9]+)?)", raw_prompt, re.IGNORECASE)
            if m:
                op = (m.group(1) or "").strip()
                try:
                    value = float(m.group(2))
                    if op in {">", ">=", "=>", "≥"} or "이상" in raw_prompt:
                        out["pi_min"] = value
                    else:
                        out["pi_max"] = value
                except Exception:
                    pass
    overrides, errors = _extract_prompt_overrides(raw_prompt)
    if overrides:
        out.update(overrides)
    return out, errors


def route_prompt(prompt: str) -> dict[str, object]:
    routed, _ = route_prompt_with_errors(prompt)
    return routed


def request_from_prompt(*, prompt: str, target_fasta: str, target_pdb: str) -> PipelineRequest:
    routed = route_prompt(prompt)
    kwargs = dict(routed)
    return PipelineRequest(target_fasta=target_fasta or "", target_pdb=target_pdb or "", **kwargs)  # type: ignore[arg-type]


def _normalize_contig(raw: str) -> str:
    value = re.sub(r"\s+", "", raw or "")
    return value.replace(":", "")


def plan_from_prompt(
    *,
    prompt: str,
    target_fasta: str | None = None,
    target_pdb: str | None = None,
    rfd3_input_pdb: str | None = None,
    rfd3_contig: str | None = None,
    diffdock_ligand_smiles: str | None = None,
    diffdock_ligand_sdf: str | None = None,
) -> dict[str, object]:
    raw_prompt = str(prompt or "")
    p = raw_prompt.strip().lower()
    routed, errors = route_prompt_with_errors(raw_prompt)
    routed = dict(routed)

    if rfd3_contig and not routed.get("rfd3_contig"):
        routed["rfd3_contig"] = _normalize_contig(rfd3_contig)

    if "rfd3_contig" not in routed:
        m = _CONTIG_RE.search(raw_prompt)
        if m:
            routed["rfd3_contig"] = _normalize_contig(m.group(1))

    wants_rfd3 = bool(re.search(r"rfd3|rfdiff|diffusion", p))
    wants_bioemu = bool(re.search(r"bioemu", p))
    wants_diffdock = bool(re.search(r"diffdock|ligand|substrate", p))

    has_target = bool(str(target_fasta or "").strip() or str(target_pdb or "").strip())
    has_rfd3_pdb = bool(str(rfd3_input_pdb or "").strip())
    has_diffdock_ligand = bool(str(diffdock_ligand_smiles or "").strip() or str(diffdock_ligand_sdf or "").strip())

    missing: list[str] = []
    questions: list[dict[str, object]] = []

    if not has_target and not has_rfd3_pdb:
        missing.append("target_input")
        questions.append(
            {
                "id": "target_input",
                "question": "Provide target_pdb or target_fasta (raw text).",
                "required": True,
            }
        )

    if wants_rfd3:
        if not has_rfd3_pdb:
            missing.append("rfd3_input_pdb")
            questions.append(
                {
                    "id": "rfd3_input_pdb",
                    "question": "Provide rfd3_input_pdb text (raw PDB).",
                    "required": True,
                }
            )

    if wants_bioemu and "bioemu_use" not in routed:
        routed["bioemu_use"] = True

    if wants_diffdock and not has_diffdock_ligand:
        missing.append("diffdock_ligand")
        questions.append(
            {
                "id": "diffdock_ligand",
                "question": "Provide diffdock_ligand_smiles or diffdock_ligand_sdf if ligand coords are missing.",
                "required": False,
            }
        )

    if "stop_after" not in routed:
        questions.append(
                {
                    "id": "stop_after",
                    "question": "Where to stop? (msa/rfd3/bioemu/design/soluprot/af2/wt_diff)",
                    "required": False,
                    "default": "novelty",
                }
            )
    if "start_from" not in routed:
        questions.append(
            {
                "id": "start_from",
                "question": "Where to start? (msa/rfd3/bioemu/design/soluprot/af2/wt_diff)",
                "required": False,
                "default": "msa",
            }
        )

    if "af2_max_candidates_per_tier" not in routed:
        questions.append(
            {
                "id": "af2_max_candidates_per_tier",
                "question": "ColabFold per tier candidate count (top SoluProt score first, 0=all).",
                "required": False,
                "default": 0,
            }
        )
    if "af2_provider" not in routed:
        questions.append(
            {
                "id": "af2_provider",
                "question": "Structure prediction provider? (colabfold/af2)",
                "required": False,
                "default": "colabfold",
            }
        )

    if "num_seq_per_tier" not in routed:
        questions.append(
            {
                "id": "num_seq_per_tier",
                "question": "ProteinMPNN sequences per tier per backbone.",
                "required": False,
                "default": 2,
            }
        )

    if "design" in p and "design_chains" not in routed and has_target:
        questions.append(
            {
                "id": "design_chains",
                "question": "Which chains to design? (default: all)",
                "required": False,
            }
        )

    return {
        "routed_request": routed,
        "missing": missing,
        "questions": questions,
        "errors": errors,
    }
