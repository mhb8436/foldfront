/**
 * 플랫폼 API 클라이언트.
 *
 * 현행 프론트엔드는 fetch 를 화면 코드에 흩어 두었다(app.js 안 18곳).
 * 호출 표면을 여기 한 곳에 모아 화면이 경로와 응답 형태를 직접 알지 않게 한다.
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
    // FastAPI 는 오류를 {detail: "..."} 로 낸다
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // 본문이 JSON 이 아니면 상태줄을 쓴다
    }
    throw new ApiError(detail, response.status)
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
  // ------------------------------------------------------------ 실행
  listRuns: (params: { status?: RunStatus; project_id?: string; limit?: number } = {}) =>
    request<Listed<Run>>(`/runs${query(params)}`),

  getRun: (runId: string) => request<Run>(`/runs/${encodeURIComponent(runId)}`),

  listEvents: (runId: string, limit = 200) =>
    request<Listed<RunEvent>>(`/runs/${encodeURIComponent(runId)}/events${query({ limit })}`),

  /** 산출물 실체를 내려받는 주소. 구조 뷰어가 이 주소를 그대로 문다 */
  artifactUrl: (runId: string, path: string) =>
    `${BASE}/runs/${encodeURIComponent(runId)}/artifacts/content${query({ path })}`,

  artifactText: async (runId: string, path: string) => {
    const r = await fetch(api.artifactUrl(runId, path))
    if (!r.ok) throw new ApiError(`산출물을 읽지 못했습니다 (${r.status})`, r.status)
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

  // ------------------------------------------------------------ 워크플로
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

  // ------------------------------------------------------------ 모델
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

  approveModel: (modelId: string, version: string, approvedBy: string, decision = 'approved') =>
    request<ModelVersion>(
      `/models/${encodeURIComponent(modelId)}/${encodeURIComponent(version)}/approve${query({
        approved_by: approvedBy,
        decision,
      })}`,
      { method: 'POST' },
    ),

  // ------------------------------------------------------------ 작업
  jobStats: () => request<{ by_status: Record<string, number>; total: number }>('/jobs/stats'),

  reclaimJobs: () => request<{ reclaimed: number }>('/jobs/reclaim', { method: 'POST' }),

  // ------------------------------------------------------------ 운영
  listAudit: (params: { actor_id?: string; action?: string; limit?: number } = {}) =>
    request<Listed<Record<string, unknown>>>(`/audit${query(params)}`),

  health: () => fetch('/healthz').then((r) => r.json()),
}
