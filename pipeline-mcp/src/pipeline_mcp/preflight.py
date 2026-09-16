from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .bio.fasta import parse_fasta
from .bio.pdb import ligand_atoms_present
from .bio.pdb import normalize_structure_text
from .bio.pdb import residues_by_chain
from .bio.pdb import sequence_by_chain
from .models import PipelineRequest
from .pipeline import PipelineRunner
from .pipeline import _clean_protein_sequence
from .pipeline import _bioemu_active
from .pipeline import _diffdock_requested
from .pipeline import _normalize_af2_provider
from .pipeline import _resolve_af2_model_preset
from .pipeline import _rfd3_active
from .pipeline import _split_multichain_sequence
from .pipeline import _validate_af2_chain_sequences
from .storage import resolve_run_path


_STAGE_ORDER = ["msa", "rfd3", "bioemu", "design", "soluprot", "af2", "novelty"]

_RESUME_OUTPUT_REQUIREMENTS = {
    "soluprot": {
        "globs": ("tiers/*/proteinmpnn.json", "tiers/*/designs.fasta", "tiers/*/designs_pi_filtered.fasta"),
        "message": "start_from='soluprot' requires existing Design/ProteinMPNN outputs in the current run.",
    },
    "af2": {
        "globs": ("tiers/*/designs_filtered.fasta",),
        "message": "start_from='af2' requires SoluProt-passed sequences in the current run.",
    },
    "novelty": {
        "globs": ("tiers/*/af2_selected.fasta", "tiers/*/relax_selected.fasta"),
        "message": "start_from='novelty' requires AF2-selected sequences (or relax-selected sequences) in the current run.",
    },
}


def _normalize_stage(value: str | None) -> str | None:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"wt_diff", "wtdiff"}:
        raw = "novelty"
    if not raw:
        return None
    if raw not in _STAGE_ORDER:
        return None
    return raw


def _needs_stage(start_from: str | None, stop_after: str | None, stage: str) -> bool:
    normalized_stage = _normalize_stage(stage)
    if normalized_stage is None:
        return True
    start = _normalize_stage(start_from)
    stop = _normalize_stage(stop_after)
    stage_index = _STAGE_ORDER.index(normalized_stage)
    if start is not None and stage_index < _STAGE_ORDER.index(start):
        return False
    if stop is not None and stage_index > _STAGE_ORDER.index(stop):
        return False
    return True


def _has_fixed_positions_extra(request: PipelineRequest) -> bool:
    if not isinstance(request.fixed_positions_extra, dict):
        return False
    for positions in request.fixed_positions_extra.values():
        if isinstance(positions, list) and positions:
            return True
    return False


def _run_has_nonempty_artifacts(output_root: str, run_id: str, patterns: tuple[str, ...]) -> bool:
    if not output_root or not run_id or not patterns:
        return False
    try:
        root = resolve_run_path(output_root, run_id)
    except Exception:
        return False
    if not root.exists():
        return False
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and path.stat().st_size > 0:
                return True
    return False


def preflight_request(request: PipelineRequest, runner: PipelineRunner, *, run_id: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    required_inputs: list[dict[str, object]] = []
    detected: dict[str, object] = {}

    start_from_raw = str(getattr(request, "start_from", "") or "").strip().lower() or None
    start_from = _normalize_stage(start_from_raw)
    if start_from_raw is not None and start_from is None:
        errors.append(
            f"Unknown start_from={start_from_raw!r}; expected one of {_STAGE_ORDER[:-1] + ['wt_diff']}."
        )

    stop_after_raw = str(request.stop_after or "").strip().lower() or None
    stop_after = _normalize_stage(stop_after_raw)
    if stop_after_raw is not None and stop_after is None:
        warnings.append(
            f"Unknown stop_after={stop_after_raw!r}; expected one of {_STAGE_ORDER[:-1] + ['wt_diff']}; defaulting to full pipeline."
        )

    normalized_run_id = str(run_id or "").strip()
    if normalized_run_id:
        detected["run_id"] = normalized_run_id

    if start_from is not None and stop_after is not None:
        if _STAGE_ORDER.index(start_from) > _STAGE_ORDER.index(stop_after):
            errors.append(
                f"start_from={start_from!r} must be earlier than or equal to stop_after={stop_after!r}."
            )

    target_fasta = str(request.target_fasta or "").strip()
    target_pdb = normalize_structure_text(str(request.target_pdb or "")).strip()
    has_target = bool(target_fasta or target_pdb)
    rfd3_active = _rfd3_active(request)
    bioemu_active = _bioemu_active(request)
    diffdock_requested = _diffdock_requested(request)
    af2_provider = _normalize_af2_provider(getattr(request, "af2_provider", None))
    af2_client = runner.colabfold if af2_provider == "colabfold" else runner.af2
    if af2_client is None and af2_provider == "colabfold" and runner.af2 is not None:
        # Backward-compatible fallback for older deployments that only configured AF2.
        af2_provider = "af2"
        af2_client = runner.af2
        warnings.append("af2_provider='colabfold' requested but ColabFold is not configured; using AlphaFold2 fallback.")
    af2_provider_label = "ColabFold" if af2_provider == "colabfold" else "AlphaFold2"
    detected["af2_provider"] = af2_provider
    bioemu_sequence = _clean_protein_sequence(str(request.bioemu_sequence or ""))
    has_bioemu_sequence = bool(bioemu_sequence)

    if stop_after == "bioemu" and not bioemu_active:
        errors.append("stop_after='bioemu' requires bioemu_use=true.")
    if stop_after == "rfd3" and not rfd3_active:
        errors.append(
            "stop_after='rfd3' requires rfd3_use=true and RFD3 inputs (for example target_pdb, rfd3_input_pdb, or rfd3_inputs_text)."
        )

    if start_from in _RESUME_OUTPUT_REQUIREMENTS:
        requirement = _RESUME_OUTPUT_REQUIREMENTS[start_from]
        if not _run_has_nonempty_artifacts(
            runner.output_root,
            normalized_run_id,
            tuple(requirement["globs"]),
        ):
            errors.append(str(requirement["message"]))

    if not has_target and not rfd3_active and not has_bioemu_sequence:
        errors.append("One of target_fasta or target_pdb (or rfd3 inputs) is required.")
        required_inputs.append(
            {
                "id": "target_input",
                "message": "Provide target_fasta or target_pdb (raw text).",
                "required": True,
            }
        )

    query_seq = ""
    if target_fasta:
        try:
            records = parse_fasta(target_fasta)
            if not records:
                errors.append("target_fasta parse failed: no FASTA records found.")
            else:
                query_seq = str(records[0].sequence or "").strip()
                if not query_seq:
                    errors.append("target_fasta parse failed: empty sequence.")
        except Exception as exc:
            errors.append(f"target_fasta parse failed: {exc}")

    pdb_chains: list[str] | None = None
    if target_pdb:
        try:
            residues = residues_by_chain(target_pdb, only_atom_records=True)
            if not residues:
                errors.append("target_pdb parse failed: no ATOM records found.")
            else:
                pdb_chains = sorted(residues.keys())
                detected["pdb_chains"] = pdb_chains
                if request.design_chains:
                    missing = [c for c in request.design_chains if c not in residues]
                    if missing:
                        errors.append(f"design_chains not found in target_pdb: {missing}")
        except Exception as exc:
            errors.append(f"target_pdb parse failed: {exc}")

    # A multi-chain or multi-model target_pdb is reduced to a single design chain;
    # surface that here (relaxed policy: auto-select but warn) so it is visible
    # both in the UI and to MCP callers via pipeline.preflight.
    if target_pdb and pdb_chains and len(pdb_chains) > 1:
        if not request.design_chains and not str(request.target_fasta or "").strip():
            warnings.append(
                f"target_pdb has {len(pdb_chains)} chains ({', '.join(pdb_chains)}) but no "
                "design_chains or target_fasta; one chain will be auto-selected (longest "
                "protein chain). Set design_chains to choose the intended chain/domain."
            )
    # Detect multi-model (NMR) input on the raw text: normalize_structure_text has
    # already reduced target_pdb to the first model, so count on request.target_pdb.
    raw_models = sum(
        1 for line in str(request.target_pdb or "").splitlines() if line[:5] == "MODEL"
    )
    if raw_models > 1:
        warnings.append(
            f"target_pdb contains {raw_models} models (e.g. an NMR ensemble); only the "
            "first model is used."
        )

    if target_pdb and (request.ligand_resnames or request.ligand_atom_chains):
        try:
            has_ligand = ligand_atoms_present(
                target_pdb,
                chains=request.design_chains,
                ligand_resnames=request.ligand_resnames,
                ligand_atom_chains=request.ligand_atom_chains,
            )
            if not has_ligand:
                warnings.append(
                    "ligand_resnames/ligand_atom_chains provided but no matching ligand atoms found in target_pdb."
                )
        except Exception as exc:
            warnings.append(f"ligand mask check failed: {exc}")

    stops_before_design = (
        start_from in {"design", "soluprot", "af2", "novelty"}
        or stop_after == "msa"
        or (stop_after == "rfd3" and rfd3_active)
        or (stop_after == "bioemu" and bioemu_active)
    )

    if (
        (not target_pdb)
        and (not request.dry_run)
        and (not stops_before_design)
        and (not _has_fixed_positions_extra(request))
        and (not (request.ligand_atom_chains or []))
        and (not rfd3_active)
    ):
        required_inputs.append(
            {
                "id": "fixed_positions_extra",
                "message": (
                    "Sequence-only input requires fixed_positions_extra (1-based query/FASTA numbering) or "
                    "a target_pdb with ligand masking (ligand_resnames/ligand_atom_chains)."
                ),
                "required": True,
            }
        )

    if request.conservation_tiers is not None and len(request.conservation_tiers) == 0:
        errors.append("conservation_tiers cannot be empty.")

    # Endpoint/service availability checks (honor auto_recover).
    def _warn_or_error(msg: str) -> None:
        if request.auto_recover:
            warnings.append(msg)
        else:
            errors.append(msg)

    if _needs_stage(start_from, stop_after, "msa") and runner.mmseqs is None:
        _warn_or_error("MMseqs client not configured; MSA will fall back to query-only if auto_recover is enabled.")

    if _needs_stage(start_from, stop_after, "design") and runner.proteinmpnn is None:
        _warn_or_error("ProteinMPNN client not configured; design will fall back to dummy sequences if auto_recover is enabled.")

    if _needs_stage(start_from, stop_after, "soluprot") and runner.soluprot is None:
        warnings.append("SoluProt service not configured; solubility filtering will be skipped.")

    if _needs_stage(start_from, stop_after, "af2") and af2_client is None:
        _warn_or_error(f"{af2_provider_label} not configured; AF2 scoring will be skipped if auto_recover is enabled.")

    if request.wt_compare and runner.soluprot is None:
        warnings.append("WT compare requested but SoluProt service is not configured.")

    if request.wt_compare and af2_client is None:
        warnings.append(f"WT compare requested but {af2_provider_label} is not configured.")

    if request.relax_enabled and runner.rosetta_relax is None:
        _warn_or_error("Rosetta relax is not configured; relax filtering will be skipped if auto_recover is enabled.")

    if request.surface_only and (not target_pdb) and af2_client is None and (not rfd3_active):
        _warn_or_error(
            f"surface_only requested but target_pdb is missing and {af2_provider_label} is not configured."
        )

    if rfd3_active and runner.rfd3 is None:
        _warn_or_error("RFD3 requested but endpoint is not configured (set RFD3_ENDPOINT_ID).")

    if _needs_stage(start_from, stop_after, "bioemu") and bioemu_active and runner.bioemu is None:
        _warn_or_error("BioEmu requested but endpoint is not configured (set BIOEMU_ENDPOINT_ID).")

    if diffdock_requested and runner.diffdock is None:
        _warn_or_error("DiffDock requested but endpoint is not configured (set DIFFDOCK_ENDPOINT_ID).")

    # AF2 input validation (only if AF2 stage is needed and sequence is available).
    if _needs_stage(start_from, stop_after, "af2"):
        seq_for_af2 = ""
        if query_seq:
            seq_for_af2 = query_seq
        elif target_pdb:
            try:
                seqs = sequence_by_chain(target_pdb, chains=request.design_chains)
                if seqs:
                    order = request.design_chains or sorted(seqs.keys())
                    parts = [_clean_protein_sequence(seqs.get(c, "")) for c in order if seqs.get(c)]
                    seq_for_af2 = "/".join(p for p in parts if p)
            except Exception as exc:
                warnings.append(f"AF2 precheck skipped: failed to extract sequence from target_pdb ({exc})")

        if seq_for_af2:
            chain_count = len(_split_multichain_sequence(seq_for_af2))
            resolved_preset = _resolve_af2_model_preset(request.af2_model_preset, chain_count=chain_count)
            detected["af2_model_preset_resolved"] = resolved_preset
            try:
                _validate_af2_chain_sequences(
                    seq_for_af2,
                    model_preset=resolved_preset,
                    chain_ids=request.design_chains,
                )
            except Exception as exc:
                errors.append(str(exc))

    ok = not errors and not required_inputs
    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "required_inputs": required_inputs,
        "detected": detected,
        "normalized_request": asdict(request),
    }
