from __future__ import annotations

from dataclasses import dataclass, field
import os


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class SequenceRecord:
    id: str
    sequence: str
    header: str | None = None
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineRequest:
    target_fasta: str
    target_pdb: str
    evolution_mode: bool = False
    evolution_initial_samples: int = 30
    evolution_rounds: int = 4
    evolution_samples_per_round: int = 20
    evolution_pool_size: int = 1000
    evolution_oracle_samples: int = 20
    evolution_label_source: str = "experimental"
    evolution_objective_metric: str = "activity"
    evolution_experiment_source_run_id: str | None = None
    evolution_surrogate_model: str = "rf"
    use_memory_bank: bool = False
    surrogate_triage_enabled: bool = False
    surrogate_triage_scope: str = "per_tier"
    surrogate_triage_initial_samples: int = 30
    surrogate_triage_top_k: int = 20
    surrogate_triage_model: str | list[str] = "auto"
    surrogate_triage_comparator_models: str | list[str] = field(
        default_factory=lambda: ["rf", "ridge", "lightgbm", "xgboost"]
    )
    surrogate_triage_ensemble_models: str | list[str] = field(default_factory=list)
    surrogate_triage_cv_folds: int = 5
    project_id: str | None = None
    round_id: str | None = None

    rfd3_use: bool | None = None
    rfd3_inputs: dict[str, object] | None = None
    rfd3_inputs_text: str | None = None
    rfd3_input_files: dict[str, str] | None = None
    rfd3_input_pdb: str | None = None
    rfd3_mode: str | None = None
    rfd3_spec_name: str = "spec-1"
    rfd3_contig: str | list[str] | None = None
    rfd3_hotspots: str | list[str] | None = None
    rfd3_infer_ori_strategy: str | None = None
    rfd3_is_non_loopy: bool | None = None
    rfd3_unindex: str | list[str] | None = None
    rfd3_length: str | list[str] | None = None
    rfd3_select_fixed_atoms: str | list[str] | dict[str, str] | None = None
    rfd3_ligand: str | list[str] | None = None
    rfd3_select_unfixed_sequence: str | None = None
    rfd3_cli_args: str | None = None
    rfd3_env: dict[str, str] | None = None
    rfd3_design_index: int = 0
    rfd3_use_ensemble: bool = False
    rfd3_max_return_designs: int = 10
    rfd3_partial_t: float | None = 5.0
    rfd3_sampling_strategy: str | None = None
    rfd3_fail_on_duplicate_backbones: bool = False
    rfd3_target_rmsd_cutoff: float | None = 2.0
    rfd3_target_gate_chains: list[str] | None = None
    rfd3_max_attempted_designs: int | None = None
    backbone_filter_use_dssp: bool = True

    bioemu_use: bool = False
    bioemu_sequence: str | None = None
    bioemu_num_samples: int = 50
    bioemu_batch_size_100: int | None = None
    bioemu_model_name: str = "bioemu-v1.1"
    bioemu_filter_samples: bool = True
    bioemu_base_seed: int | None = None
    bioemu_steering_config_text: str | None = None
    bioemu_max_return_structures: int = 10
    bioemu_target_rmsd_cutoff: float | None = 2.0
    bioemu_max_attempted_structures: int | None = None
    bioemu_env: dict[str, str] | None = None

    diffdock_ligand_smiles: str | None = None
    diffdock_ligand_sdf: str | None = None
    diffdock_config: str = "default_inference_args.yaml"
    diffdock_extra_args: str | None = None
    diffdock_cuda_visible_devices: str | None = None

    design_chains: list[str] | None = None

    # Extra fixed positions to union into each tier (1-based, query/FASTA numbering).
    # If both target_fasta and target_pdb are provided, positions are mapped to the PDB chain indices
    # via the query→PDB alignment written to query_pdb_alignment.json.
    fixed_positions_extra: dict[str, list[int]] | None = None

    conservation_tiers: list[float] = field(default_factory=lambda: [0.3, 0.5, 0.7])
    selected_tiers: list[float] | None = None
    conservation_mode: str = "quantile"  # quantile | threshold
    conservation_weighting: str = "none"  # none | mmseqs_cluster
    conservation_cluster_method: str = "linclust"
    conservation_cluster_min_seq_id: float = 0.9
    conservation_cluster_coverage: float | None = None
    conservation_cluster_cov_mode: int | None = None
    conservation_cluster_kmer_per_seq: int | None = None

    ligand_mask_distance: float = 6.0
    ligand_resnames: list[str] | None = None
    ligand_atom_chains: list[str] | None = None
    ligand_mask_use_original_target: bool = True
    surface_only: bool = False
    surface_min_rel: float = 0.2
    surface_min_abs: float = 10.0

    pdb_strip_nonpositive_resseq: bool = True
    pdb_renumber_resseq_from_1: bool = False

    num_seq_per_tier: int = 2
    batch_size: int = 1
    sampling_temp: float = 0.1
    seed: int = 0

    soluprot_cutoff: float = 0.5
    pi_min: float | None = None
    pi_max: float | None = None

    af2_model_preset: str = "auto"  # auto | monomer | multimer (and variants)
    af2_db_preset: str = "full_dbs"
    af2_max_template_date: str = "2020-05-14"
    af2_extra_flags: str | None = None
    af2_provider: str = "colabfold"  # colabfold | af2
    af2_plddt_cutoff: float = 85.0
    af2_rmsd_cutoff: float = 2.0
    # Limit AF2 predictions per tier using top SoluProt-scored candidates (<=0 means no limit).
    af2_max_candidates_per_tier: int = 0
    af2_top_k: int = 0
    af2_sequence_ids: list[str] | None = None
    relax_enabled: bool = False
    relax_score_per_residue_cutoff: float | None = None
    relax_nstruct: int = 1
    relax_extra_flags: str | None = None

    mmseqs_target_db: str = "uniref90"
    mmseqs_max_seqs: int = 3000
    mmseqs_threads: int = 4
    mmseqs_use_gpu: bool = field(
        default_factory=lambda: _env_true("PIPELINE_MMSEQS_USE_GPU")
        or _env_true("MMSEQS_USE_GPU")
    )

    novelty_enabled: bool = False
    novelty_target_db: str = "uniref90"

    msa_min_coverage: float = 0.0
    msa_min_identity: float = 0.0

    query_pdb_min_identity: float = 0.9
    query_pdb_policy: str = "error"  # error | warn | ignore

    start_from: str | None = (
        None  # msa | rfd3 | bioemu | design | soluprot | af2 | novelty (wt_diff alias)
    )
    stop_after: str | None = (
        None  # rfd3 | bioemu | msa | design | soluprot | af2 | novelty (wt_diff alias)
    )
    force: bool = False
    dry_run: bool = False
    agent_panel_enabled: bool = True
    auto_recover: bool = True
    wt_compare: bool = True
    mask_consensus_apply: bool = False


@dataclass(frozen=True)
class TierResult:
    tier: float
    fixed_positions: dict[str, list[int]]
    proteinmpnn_native: SequenceRecord | None
    proteinmpnn_samples: list[SequenceRecord]
    mutation_report_path: str | None = None
    mutations_by_position_tsv: str | None = None
    mutations_by_position_svg: str | None = None
    mutations_by_sequence_tsv: str | None = None
    soluprot_scores: dict[str, float] | None = None
    passed_ids: list[str] | None = None
    af2: dict[str, object] | None = None
    af2_selected_ids: list[str] | None = None
    relax_selected_ids: list[str] | None = None
    novelty_tsv: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    output_dir: str
    msa_a3m_path: str | None
    msa_filtered_a3m_path: str | None
    msa_tsv_path: str | None
    conservation_path: str | None
    ligand_mask_path: str | None
    surface_mask_path: str | None
    tiers: list[TierResult]
    errors: list[str] = field(default_factory=list)
