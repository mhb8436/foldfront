/**
 * The platform API client.
 *
 * The original console scatters fetch across its screens - eighteen call sites
 * in app.js. Gathering them here means a screen never states a path or knows
 * the shape of a response, so changing either is one file.
 */

export type RunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type NodeKind = 'model' | 'transform' | 'branch' | 'fanout' | 'join'

export interface StageState {
  name: string
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  model_id: string | null
  model_version: string | null
  error: string | null
  metrics: Record<string, unknown>
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
  workflow_id: string | null
  workflow_version: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
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
