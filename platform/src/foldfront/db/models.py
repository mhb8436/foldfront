"""MongoDB document schemas.

What is stored here: design history per run, the inputs and model versions a
run used, artifact metadata, comparison and feedback data, reports and the
audit trail, DAG workflows, projects and rounds, and the job queue.

The original keeps a run as a directory of request.json, status.json and
events.jsonl. These schemas move that into MongoDB while preserving its shape.
Field names are not renamed on the way across - migration of existing runs
depends on recognising them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Doc(BaseModel):
    """Base for every document."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- run


class RunStatus(StrEnum):
    """The status values the original writes to status.json, unchanged."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageName(StrEnum):
    """The original's fixed chain. A free-form DAG is not bound to these."""

    MSA = "msa"
    RFD3 = "rfd3"
    BIOEMU = "bioemu"
    DESIGN = "design"
    SOLUPROT = "soluprot"
    AF2 = "af2"
    NOVELTY = "novelty"


class StageState(Doc):
    """State of one stage. This is also the unit a rerun restarts from."""

    name: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #  Which model, at which version, produced this
    model_id: str | None = None
    model_version: str | None = None
    endpoint_id: str | None = None
    #  Hash of the stage request, carried over from the original
    request_hash: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class Run(Doc):
    """One run. Corresponds to one run directory in the original.

    The whole of request.json goes into `request` untouched: PipelineRequest
    has 108 fields and keeps growing, so pinning a schema to it would break on
    the next addition. Only the values queries need are lifted out of it.
    """

    run_id: str
    status: RunStatus = RunStatus.PENDING
    mode: Literal["pipeline", "workflow", "binding"] = "pipeline"

    #  Ties the run to a project and round; the original carries these too
    project_id: str | None = None
    round_id: str | None = None

    #  Everything needed to reproduce the run, kept verbatim
    request: dict[str, Any] = Field(default_factory=dict)

    #  Promoted for querying; the full value stays inside request
    target_fasta_name: str | None = None
    design_chains: list[str] = Field(default_factory=list)
    conservation_tiers: list[float] = Field(default_factory=list)

    stages: list[StageState] = Field(default_factory=list)

    #  Fork lineage: which run, and which stage it branched from
    forked_from_run_id: str | None = None
    forked_from_stage: str | None = None

    #  Points at the workflow definition when the run came from a DAG
    workflow_id: str | None = None
    workflow_version: int | None = None

    #  Environment snapshot: versions and endpoint ids, never secret values
    environment: dict[str, str] = Field(default_factory=dict)

    started_at: datetime | None = None
    finished_at: datetime | None = None
    owner_id: str | None = None
    error: str | None = None


class RunEvent(Doc):
    """One line of the original events.jsonl. Append-only; never edited."""

    run_id: str
    seq: int
    level: Literal["debug", "info", "warning", "error"] = "info"
    stage: str | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- artifacts


class Artifact(Doc):
    """Artifact metadata.

    The file itself lives in object storage; only its location and properties
    are kept here. A PDB, an MSA or a report passes the 16 MB document limit
    without much effort.
    """

    run_id: str
    stage: str | None = None
    path: str  # Relative to the storage root, following the original layout
    kind: str  # pdb · fasta · a3m · json · svg · tsv · report · log
    size_bytes: int = 0
    checksum: str | None = None
    content_type: str | None = None
    #  Whether a researcher should see it, as _is_user_visible_artifact_path decided
    user_visible: bool = True
    #  Retention. Intermediate artifacts are swept once this passes
    retain_until: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- Model Registry


class ModelKind(StrEnum):
    BACKBONE = "backbone"        # RFD3 · BioEmu
    SEQUENCE = "sequence"        # ProteinMPNN
    SOLUBILITY = "solubility"    # SoluProt
    STRUCTURE = "structure"      # AF2 · ColabFold
    DOCKING = "docking"          # DiffDock
    MSA = "msa"                  # MMseqs2
    EMBEDDING = "embedding"      # ESM
    OTHER = "other"


class ResourceSpec(BaseModel):
    """What a model needs to run. The queue schedules against this."""

    gpu_count: int = 0
    gpu_memory_gb: float | None = None
    cpu_count: int | None = None
    memory_gb: float | None = None
    timeout_seconds: float = 21600.0


class ModelVersion(Doc):
    """One version of one model.

    The original tracks only a base URL, a timeout and a scope per provider.
    A registry needs more: the version itself, a container image, the input and
    output schemas, and what the model costs to run.
    """

    model_id: str          # rfd3, proteinmpnn, af2 - called by id, never by URL
    version: str           # v1.0.29 · 2026-09-01 …
    kind: ModelKind = ModelKind.OTHER
    display_name: str | None = None

    #  Where it runs - at least one of the three
    endpoint_id: str | None = None      # RunPod serverless endpoint
    base_url: str | None = None         # Self-hosted HTTP worker
    container_image: str | None = None  # Container image, tag included

    #  Input and output schemas; the router validates requests against them
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    resources: ResourceSpec = Field(default_factory=ResourceSpec)

    #  An inactive version is never routed to
    active: bool = True
    #  The version chosen when a caller names the model without one
    is_default: bool = False

    #  Approval for a model someone registered themselves
    approval_status: Literal["approved", "pending", "rejected"] = "approved"
    approved_by: str | None = None
    approved_at: datetime | None = None
    registered_by: str | None = None

    #  Where the weights live
    weights_uri: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------- DAG workflows


class NodeKind(StrEnum):
    MODEL = "model"          # Runs a model from the registry
    TRANSFORM = "transform"  # Built-in transform: filter, select, merge
    BRANCH = "branch"        # Conditional branch
    FANOUT = "fanout"        # Parallel split
    JOIN = "join"            # Parallel join


class WorkflowNode(BaseModel):
    """One node of a DAG."""

    node_id: str
    kind: NodeKind = NodeKind.MODEL
    label: str | None = None

    #  kind=MODEL: leaving the version empty routes to the default
    model_id: str | None = None
    model_version: str | None = None

    params: dict[str, Any] = Field(default_factory=dict)

    #  kind=BRANCH: true follows on_true, false follows on_false
    condition: str | None = None

    #  Canvas position, written by the editor
    position: dict[str, float] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    source: str
    target: str
    #  Which side of a branch this edge leaves by
    branch: Literal["true", "false"] | None = None


class Workflow(Doc):
    """A DAG definition, saved as a template and reused.

    Versions accumulate rather than overwrite: one document per version of the
    same workflow_id, so a run can always name the definition it used.
    """

    workflow_id: str
    version: int = 1
    name: str
    description: str | None = None

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)

    #  A template is listed for others to copy
    is_template: bool = False
    #  True for the built-in template that mirrors the fixed stage chain
    is_builtin: bool = False

    owner_id: str | None = None
    project_id: str | None = None

    #  Approval state before a user-defined workflow reaches production
    approval_status: Literal["approved", "pending", "rejected"] = "approved"


# ---------------------------------------------------------------- job queue


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Doc):
    """One item of work.

    Scheduling weighs the model's resource requirements against priority.
    A worker takes the job on a lease, so a worker that dies does not hold it
    forever - the job returns once lease_expires_at passes.
    """

    job_id: str
    run_id: str
    node_id: str | None = None   # Which node, for a DAG run
    stage: str | None = None     # Which stage, for a fixed-chain run

    status: JobStatus = JobStatus.QUEUED
    priority: int = 0            # Higher goes first

    model_id: str | None = None
    model_version: str | None = None
    resources: ResourceSpec = Field(default_factory=ResourceSpec)

    payload: dict[str, Any] = Field(default_factory=dict)

    attempts: int = 0
    max_attempts: int = 3
    leased_by: str | None = None
    lease_expires_at: datetime | None = None

    queued_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    #  What the model replied. The run keeps this too, on the stage - but a
    #  worker records the job and the stage in two writes, and a worker that
    #  dies between them would otherwise leave the work done and the result
    #  gone. Reconciliation reads it from here.
    #  None until a result is recorded. An empty dict is a real reply.
    result: dict[str, Any] | None = None


# ---------------------------------------------------------------- projects


class Project(Doc):
    """Corresponds to workspace/projects/<id>/project.json."""

    project_id: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    archived: bool = False
    tags: list[str] = Field(default_factory=list)


class Round(Doc):
    """Corresponds to projects/<id>/rounds/<round_id>.json."""

    round_id: str
    project_id: str
    name: str | None = None
    index: int = 1
    linked_run_ids: list[str] = Field(default_factory=list)
    objective: str | None = None
    archived: bool = False


class Task(Doc):
    """A unit of work inside a round."""

    task_id: str
    project_id: str
    round_id: str | None = None
    title: str
    status: Literal["todo", "doing", "done", "dropped"] = "todo"
    assignee_id: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class Feedback(Doc):
    """Corresponds to feedback.jsonl in the original run directory."""

    run_id: str
    project_id: str | None = None
    #  Identifies the candidate sequence or structure being judged
    subject_id: str | None = None
    verdict: Literal["positive", "negative", "neutral"] = "neutral"
    score: float | None = None
    comment: str | None = None
    author_id: str | None = None


class Experiment(Doc):
    """Corresponds to experiments.jsonl: results from the bench."""

    run_id: str
    project_id: str | None = None
    subject_id: str | None = None
    metric: str | None = None          # activity · expression · tm …
    value: float | None = None
    unit: str | None = None
    protocol: str | None = None
    note: str | None = None
    author_id: str | None = None


# ---------------------------------------------------------------- reports, audit


class Report(Doc):
    """Generated reports and hand-edited revisions, kept side by side."""

    report_id: str
    run_id: str
    language: Literal["ko", "en"] = "ko"
    format: Literal["markdown", "html", "pdf"] = "markdown"
    version: int = 1
    #  A large body moves to an artifact; only the reference stays here
    body: str | None = None
    artifact_path: str | None = None
    generated_by: Literal["auto", "manual"] = "auto"
    author_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class AuditLog(Doc):
    """Audit trail. Append-only; never edited, never deleted.

    What it records: sign-in, run requests, model registration, configuration
    changes, operational patches, and downloads of results.
    """

    actor_id: str | None = None
    actor_role: str | None = None
    action: str                     # run.create · model.register · config.update …
    target_type: str | None = None  # run · model · workflow · config
    target_id: str | None = None
    result: Literal["success", "failure", "denied"] = "success"
    source_ip: str | None = None
    user_agent: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- users, roles


class Role(StrEnum):
    """The original has only admin and user; four roles are needed here."""

    ADMIN = "admin"            # Operations, model registration, settings
    RESEARCHER = "researcher"  # Runs, analysis, reports
    VIEWER = "viewer"          # Read-only
    SERVICE = "service"        # Machine account for MCP and API callers


class User(Doc):
    user_id: str
    subject: str | None = None   # OIDC sub, as the original oidc.py reads it
    email: str | None = None
    display_name: str | None = None
    roles: list[Role] = Field(default_factory=lambda: [Role.VIEWER])
    active: bool = True
    last_login_at: datetime | None = None
