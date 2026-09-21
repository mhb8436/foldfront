/**
 * The platform API client.
 *
 * The original console scatters fetch across its screens - eighteen call sites
 * in app.js. Gathering them here means a screen never states a path or knows
 * the shape of a response, so changing either is one file.
 */

export type RunStatus = 'pending' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
export type NodeKind = 'model' | 'transform' | 'branch' | 'fanout' | 'join' | 'checkpoint'

export interface StageState {
  name: string
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  model_id: string | null
  model_version: string | null
  error: string | null
  metrics: Record<string, unknown>
  /*  Raised by a rerun, so a metric can be shown as the second attempt's. */
  attempt: number
  /*  kind=checkpoint only: who answered the gate, and what they wrote. */
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
}

export interface Run {
  run_id: string
  status: RunStatus
  mode: string
  project_id: string | null
  round_id: string | null
  request: Record<string, unknown>
  stages: StageState[]
  forked_from_run_id: string | null
  forked_from_stage: string | null
  /*  Set while the run is held. `paused_at_node` names the checkpoint that
      is holding it; empty means a person paused it by hand. */
  paused_at: string | null
  paused_by: string | null
  paused_reason: string | null
  paused_at_node: string | null
  workflow_id: string | null
  workflow_version: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

/** One ranked candidate. The original computes every number here. */
export interface HitRow {
  rank: number
  seq_id: string
  tier: number | null
  source: string | null
  sequence: string | null
  soluprot: number | null
  plddt: number | null
  rmsd: number | null
  rmsd_target: number | null
  relax: number | null
  /*  WT Diff — how far this design has moved from the wild type. */
  wt_identity: number | null
  wt_identity_pct: number | null
  wt_diff_count: number | null
  wt_compare_len: number | null
  wt_diff_ratio: number | null
  wt_diff_pct: number | null
  novelty: number | null
  soluprot_passed: boolean
  af2_candidate: boolean
  af2_selected: boolean
  score: number | null
  af2_ranked_pdb_path: string | null
}

export interface HitList {
  run_id: string
  generated_at: string
  weights: Record<string, number>
  min_score: number
  rmsd_ref: number
  relax_enabled: boolean
  total_rows: number
  filtered_rows: number
  rows: HitRow[]
  stats: Record<string, unknown>
  completeness?: Record<string, unknown>
  /*  Set when there is nothing to rank, so the screen can say which. */
  empty_reason?: string
}

/** One reading of one stage, with the original's threshold behind it. */
export interface QualitySignal {
  stage: string
  level: 'info' | 'warning' | 'error'
  message: string
  advice: string | null
  evidence: Record<string, unknown>
  source: string | null
}

export interface QualityReport {
  run_id: string
  signals: QualitySignal[]
  counts: Record<string, number>
  recorded_events: Array<Record<string, unknown>>
}

/** One thing the router wants settled before GPU time is spent. */
export interface PlanQuestion {
  id: string
  question: string
  required: boolean
  default?: unknown
}

export interface Plan {
  prompt: string
  /*  The original router's own three answers, unchanged. */
  routed_request: Record<string, unknown>
  missing: string[]
  questions: PlanQuestion[]
  errors: string[]
  /*  What we made of them: a draft the studio can open. */
  stages: string[]
  workflow: Workflow
  ready: boolean
  /*  True when nothing in the sentence steered the plan. */
  defaulted: boolean
  note: string
}

export interface RunEvent {
  run_id: string
  seq: number
  level: 'debug' | 'info' | 'warning' | 'error'
  stage: string | null
  message: string
  created_at: string
}

export interface Artifact {
  run_id: string
  stage: string | null
  path: string
  kind: string
  size_bytes: number
  user_visible: boolean
}

export interface WorkflowNode {
  node_id: string
  kind: NodeKind
  label: string | null
  model_id: string | null
  model_version: string | null
  params: Record<string, unknown>
  condition: string | null
  position: { x?: number; y?: number }
}

export interface WorkflowEdge {
  source: string
  target: string
  branch: 'true' | 'false' | null
}

export interface Workflow {
  workflow_id: string
  version: number
  name: string
  description: string | null
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  is_template: boolean
  is_builtin: boolean
}

export interface ModelVersion {
  model_id: string
  version: string
  kind: string
  display_name: string | null
  endpoint_id: string | null
  base_url: string | null
  container_image: string | null
  resources: { gpu_count: number; gpu_memory_gb: number | null; timeout_seconds: number }
  active: boolean
  is_default: boolean
  approval_status: 'approved' | 'pending' | 'rejected'
}

export interface Job {
  job_id: string
  run_id: string
  node_id: string | null
  status: string
  priority: number
  model_id: string | null
  attempts: number
}

export interface Identity {
  user_id: string
  email: string
  roles: string[]
  authenticated: boolean
  auth_mode: 'oidc' | 'disabled'
}

export interface Project {
  project_id: string
  name: string
  description: string | null
  owner_id: string | null
  archived: boolean
  tags: string[]
  created_at?: string | null
}

export interface Round {
  round_id: string
  project_id: string
  name: string | null
  /** 1, 2, 3 - which pass of the redesign loop this is. */
  index: number
  linked_run_ids: string[]
  objective: string | null
  archived: boolean
  created_at?: string | null
}

export interface Notice {
  id: string
  kind: string
  /** action - waits on a decision. warning - a state that is already wrong. */
  severity: 'action' | 'warning'
  title: string
  detail: string
  href: string | null
  at: string | null
  /** What the notice is about, when there is something to act on. */
  target_id: string | null
}

export interface UserAccount {
  user_id: string
  subject: string | null
  email: string | null
  display_name: string | null
  roles: string[]
  active: boolean
  last_login_at: string | null
}

export interface Summary {
  runs: {
    total: number
    by_status: Record<string, number>
    recent: Array<{
      run_id: string
      status: RunStatus
      workflow_id: string | null
      stages_done: number
      stages_total: number
      started_at: string | null
      finished_at: string | null
    }>
  }
  jobs: { by_status: Record<string, number>; total: number; gpu_in_use: number }
  models: {
    total: number
    active: number
    pending_approval: Array<{ model_id: string; version: string; kind: string }>
  }
  workflows: {
    total: number
    items: Array<{
      workflow_id: string
      name: string
      version: number
      nodes: number
      is_builtin: boolean
    }>
  }
  audit: Array<{
    action: string
    actor_id: string | null
    target_id: string | null
    created_at: string | null
  }>
  /** What the numbers above cover. Null project means the whole installation. */
  scope: { project_id: string | null }
}

export interface Preflight {
  ok: boolean
  graph_error: string | null
  resolved: Array<Record<string, unknown>>
  errors: string[]
  total_gpu: number
  node_count?: number
  levels?: string[][]
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** Stable identifier such as `run.not_found`. Branch on this, not on the text. */
    readonly code: string | null = null,
    /** Values the message was built from, so a screen can use them directly. */
    readonly params: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    //  The platform answers {error: {code, message, params}}; FastAPI's own
    //  failures - validation, for one - still answer {detail}. Read both.
    let message = `${response.status} ${response.statusText}`
    let code: string | null = null
    let params: Record<string, unknown> = {}
    try {
      const body = await response.json()
      if (body?.error && typeof body.error.message === 'string') {
        message = body.error.message
        code = typeof body.error.code === 'string' ? body.error.code : null
        params = body.error.params ?? {}
      } else if (typeof body?.detail === 'string') {
        message = body.detail
      }
    } catch {
      //  Not JSON. The status line is the best that is left.
    }
    throw new ApiError(message, response.status, code, params)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function query(params: Record<string, unknown>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
  if (entries.length === 0) return ''
  return '?' + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()
}

interface Listed<T> {
  items: T[]
  count: number
}

export const api = {
  // ------------------------------------------------------------ runs
  listRuns: (
    params: {
      status?: RunStatus
      project_id?: string
      round_id?: string
      limit?: number
    } = {},
  ) => request<Listed<Run>>(`/runs${query(params)}`),

  getRun: (runId: string) => request<Run>(`/runs/${encodeURIComponent(runId)}`),

  listEvents: (runId: string, limit = 200) =>
    request<Listed<RunEvent>>(`/runs/${encodeURIComponent(runId)}/events${query({ limit })}`),

  /** Where an artifact is served. The structure viewer reads this directly. */
  artifactUrl: (runId: string, path: string) =>
    `${BASE}/runs/${encodeURIComponent(runId)}/artifacts/content${query({ path })}`,

  artifactText: async (runId: string, path: string) => {
    const r = await fetch(api.artifactUrl(runId, path))
    if (!r.ok) throw new ApiError(`산출물을 읽지 못했습니다 (${r.status})`, r.status, 'artifact.unreadable')
    return await r.text()
  },

  listArtifacts: (runId: string, stage?: string) =>
    request<Listed<Artifact> & { total_bytes: number }>(
      `/runs/${encodeURIComponent(runId)}/artifacts${query({ stage })}`,
    ),

  startRun: (body: {
    workflow_id: string
    workflow_version?: number
    request?: Record<string, unknown>
    project_id?: string
    round_id?: string
    owner_id?: string
  }) => request<Run>('/runs', { method: 'POST', body: JSON.stringify(body) }),

  forkRun: (runId: string, fromStage?: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/fork${query({ from_stage: fromStage })}`, {
      method: 'POST',
    }),

  cancelRun: (runId: string, reason?: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/cancel${query({ reason })}`, {
      method: 'POST',
    }),

  pauseRun: (runId: string, reason?: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/pause${query({ reason })}`, {
      method: 'POST',
    }),

  resumeRun: (runId: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' }),

  reviewCheckpoint: (runId: string, nodeId: string, approved: boolean, note?: string) =>
    request<{ ok: boolean; approved: boolean; queued?: string[]; status: string }>(
      `/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/review${query({ approved, note })}`,
      { method: 'POST' },
    ),

  rerunStage: (runId: string, nodeId: string) =>
    request<{ ok: boolean; reset: string[]; queued: string[]; status: string }>(
      `/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/rerun`,
      { method: 'POST' },
    ),

  copilotPlan: (body: {
    prompt: string
    target_fasta?: string
    target_pdb?: string
    rfd3_input_pdb?: string
    rfd3_contig?: string
  }) => request<Plan>('/copilot/plan', { method: 'POST', body: JSON.stringify(body) }),

  // ------------------------------------------------------------ analysis
  /*  The ranking, the WT difference and the comparison metrics are the
      original's own functions. These paths carry them, and compute none. */
  hitList: (
    runId: string,
    params: {
      limit?: number
      min_score?: number
      rmsd_ref?: number
      soluprot?: number
      plddt?: number
      rmsd?: number
      novelty?: number
    } = {},
  ) => request<HitList>(`/runs/${encodeURIComponent(runId)}/hit-list${query(params)}`),

  compareRuns: (runId: string, baselineRunId: string) =>
    request<Record<string, unknown>>(
      `/runs/${encodeURIComponent(runId)}/compare${query({ baseline_run_id: baselineRunId })}`,
    ),

  quality: (runId: string) =>
    request<QualityReport>(`/runs/${encodeURIComponent(runId)}/quality`),

  // ------------------------------------------------------------ workflows
  listWorkflows: (params: { templates_only?: boolean; project_id?: string } = {}) =>
    request<Listed<Workflow>>(`/workflows${query(params)}`),

  getWorkflow: (workflowId: string, version?: number) =>
    request<Workflow>(`/workflows/${encodeURIComponent(workflowId)}${query({ version })}`),

  workflowVersions: (workflowId: string) =>
    request<{ workflow_id: string; versions: number[] }>(
      `/workflows/${encodeURIComponent(workflowId)}/versions`,
    ),

  saveWorkflow: (workflow: Partial<Workflow>) =>
    request<{ workflow: Workflow; levels: string[][]; unregistered_models: string[] }>(
      '/workflows',
      { method: 'POST', body: JSON.stringify(workflow) },
    ),

  preflight: (workflowId: string, params: { version?: number; max_gpu?: number } = {}) =>
    request<Preflight>(`/workflows/${encodeURIComponent(workflowId)}/preflight${query(params)}`, {
      method: 'POST',
    }),

  seedBuiltin: (stages?: string[]) =>
    request<Workflow>('/workflows/builtin', {
      method: 'POST',
      body: JSON.stringify(stages ?? null),
    }),

  // ------------------------------------------------------------ models
  listModels: (params: { model_id?: string; kind?: string; active_only?: boolean } = {}) =>
    request<Listed<ModelVersion>>(`/models${query(params)}`),

  registerModel: (model: Partial<ModelVersion>) =>
    request<ModelVersion>('/models', { method: 'POST', body: JSON.stringify(model) }),

  resolveModel: (modelId: string, version?: string) =>
    request<Record<string, unknown>>(
      `/models/${encodeURIComponent(modelId)}/resolve${query({ version })}`,
    ),

  setModelActive: (modelId: string, version: string, active: boolean) =>
    request<ModelVersion>(
      `/models/${encodeURIComponent(modelId)}/${encodeURIComponent(version)}/active${query({ active })}`,
      { method: 'POST' },
    ),

  //  Who approved is taken from the verified identity, not from the caller.
  approveModel: (modelId: string, version: string, decision = 'approved') =>
    request<ModelVersion>(
      `/models/${encodeURIComponent(modelId)}/${encodeURIComponent(version)}/approve${query({
        decision,
      })}`,
      { method: 'POST' },
    ),

  // ------------------------------------------------------------ jobs
  jobStats: () => request<{ by_status: Record<string, number>; total: number }>('/jobs/stats'),

  reclaimJobs: () => request<{ reclaimed: number }>('/jobs/reclaim', { method: 'POST' }),

  // ------------------------------------------------------------ identity
  me: () => request<Identity>('/me'),

  // ------------------------------------------------------------ dashboard
  summary: (recent = 5, project_id?: string) =>
    request<Summary>(`/summary${query({ recent, project_id })}`),

  notices: () => request<Listed<Notice>>('/notices'),

  /** Bring one run back in step with its jobs. Harmless on a healthy run. */
  reconcileRun: (runId: string) =>
    request<{ ok: boolean; status: string; repaired: Array<{ node_id: string | null; did: string }> }>(
      `/runs/${encodeURIComponent(runId)}/reconcile`,
      { method: 'POST' },
    ),

  /** A fork is created waiting; this starts it from the stage it forked at. */
  startForkedRun: (runId: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/start`, { method: 'POST' }),

  reconcileRuns: () =>
    request<{ checked: number; repaired: Array<{ run_id: string }> }>('/runs/reconcile', {
      method: 'POST',
    }),

  /** Send a sequence or structure file up, and get back the path a run can name. */
  uploadInput: (file: File) => {
    const form = new FormData()
    form.append('file', file, file.name)
    //  No content type: the browser sets multipart/form-data with its
    //  boundary, and a JSON header here would make the server reject it.
    return request<{ path: string; name: string; kind: string; size_bytes: number }>('/inputs', {
      method: 'POST',
      body: form,
      headers: {},
    })
  },

  inputUsage: () =>
    request<{
      used_bytes: number
      quota_bytes: number
      retention_days: number
      items: Array<{ input_id: string; name: string; kind: string; size_bytes: number; run_ids: string[]; created_at?: string | null }>
    }>('/inputs/usage'),

  pruneInputs: (days?: number) =>
    request<{ days: number; removed: number; freed_bytes: number }>(`/inputs/prune${query({ days })}`, {
      method: 'POST',
    }),

  // -------------------------------------------------------- copilot
  copilotStatus: () =>
    request<{ available: boolean; model: string; url: string; served: string[] }>('/copilot/status'),

  copilotChat: (body: {
    messages: Array<{ role: 'user' | 'assistant'; content: string }>
    project_id?: string
    run_id?: string
  }) =>
    request<{ reply: string; context_used: string[]; model: string }>('/copilot/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // ---------------------------------------------------------- users
  listUsers: () => request<Listed<UserAccount>>('/users'),

  patchUser: (userId: string, patch: { roles?: string[]; active?: boolean }) =>
    request<UserAccount>(`/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  // ------------------------------------------------------- projects
  listProjects: (include_archived = false) =>
    request<Listed<Project>>(`/projects${query({ include_archived })}`),

  createProject: (body: { name: string; description?: string | null; tags?: string[] }) =>
    request<Project>('/projects', {
      method: 'POST',
      body: JSON.stringify({ project_id: '', archived: false, tags: [], ...body }),
    }),

  listRounds: (projectId: string) =>
    request<Listed<Round>>(`/projects/${encodeURIComponent(projectId)}/rounds`),

  createRound: (body: {
    project_id: string
    name?: string | null
    index?: number
    objective?: string | null
  }) =>
    request<Round>('/rounds', {
      method: 'POST',
      body: JSON.stringify({ round_id: '', linked_run_ids: [], archived: false, ...body }),
    }),

  // ------------------------------------------------------------ operations
  listAudit: (params: { actor_id?: string; action?: string; limit?: number } = {}) =>
    request<Listed<Record<string, unknown>>>(`/audit${query(params)}`),

  health: () => fetch('/healthz').then((r) => r.json()),
}
