import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactFlow, {
  addEdge,
  Background,
  Controls,
  MiniMap,
  Position,
  type Connection,
  type Edge,
  type Node,
  useEdgesState,
  useNodesState,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { Plus, Save } from 'lucide-react'

import { api, type NodeKind, type Workflow } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { stageTerm } from '@/lib/glossary'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/**
 * DAG Workflow Studio.
 *
 * The original fixes the stage order in code. Here a researcher composes the
 * nodes, with parallel and conditional branches, using the same vocabulary as
 * the fixed-chain run.
 *
 * Node kinds are told apart by shape rather than colour - fill, border, corner
 * radius, dashes. The console is greyscale, and the kinds have to remain
 * distinguishable when a screenshot is printed in black and white.
 */

const KIND_LABEL: Record<NodeKind, string> = {
  model: '모델',
  transform: '변환',
  branch: '조건 분기',
  fanout: '병렬 분기',
  join: '합류',
}

/** Shape per kind. CSS variables, so it follows the light and dark themes. */
function nodeStyle(kind: NodeKind): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: '8px 14px',
    fontSize: 12,
    minWidth: 124,
    borderRadius: 6,
    background: 'var(--background)',
    color: 'var(--foreground)',
    border: '1.5px solid var(--foreground)',
  }
  switch (kind) {
    case 'model':
      return { ...base, background: 'var(--foreground)', color: 'var(--background)', border: 'none' }
    case 'transform':
      return { ...base, background: 'var(--muted)', border: '1.5px solid var(--border)' }
    case 'branch':
      return { ...base, borderRadius: 999, borderWidth: 2 }
    case 'fanout':
      return { ...base, borderStyle: 'dashed', minWidth: 104 }
    case 'join':
      return { ...base, minWidth: 104 }
  }
}

function label(n: { node_id: string; kind: NodeKind; label?: string | null; model_id?: string | null; condition?: string | null }) {
  return (
    <div style={{ textAlign: 'center', lineHeight: 1.35 }}>
      <div style={{ fontWeight: 600 }}>{n.label ?? n.node_id}</div>
      <div style={{ fontSize: 10, opacity: 0.75 }}>
        {n.kind === 'model' ? (n.model_id ?? '모델 미지정') : KIND_LABEL[n.kind]}
      </div>
      {/*  The identifier stays: logs and artifact paths use it */}
      {n.kind === 'model' && stageTerm(n.model_id) && (
        <div style={{ fontSize: 9.5, opacity: 0.6 }}>{stageTerm(n.model_id)!.label}</div>
      )}
      {n.condition && (
        <div style={{ fontSize: 10, opacity: 0.75, fontFamily: 'var(--font-mono)' }}>
          {n.condition}
        </div>
      )}
    </div>
  )
}

function toFlowNodes(wf: Workflow): Node[] {
  return wf.nodes.map((n, i) => ({
    id: n.node_id,
    position: { x: n.position?.x ?? i * 180, y: n.position?.y ?? 0 },
    //  Flow runs left to right. Handles anywhere but the sides make the
    //  edges curl back on themselves.
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: { label: label(n) },
    style: nodeStyle(n.kind),
  }))
}

function edgeStyle(branch: 'true' | 'false' | null | undefined): React.CSSProperties {
  return {
    stroke: 'var(--foreground)',
    strokeWidth: branch ? 1.8 : 1.4,
    //  The false branch is dashed rather than coloured
    strokeDasharray: branch === 'false' ? '6 4' : undefined,
  }
}

function toFlowEdges(wf: Workflow): Edge[] {
  return wf.edges.map((e, i) => ({
    id: `e${i}-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    label: e.branch ? (e.branch === 'true' ? '참' : '거짓') : undefined,
    style: edgeStyle(e.branch),
    labelStyle: { fontSize: 11, fill: 'var(--foreground)' },
    labelBgStyle: { fill: 'var(--background)' },
  }))
}

export function Studio() {
  const workflows = useAsync(() => api.listWorkflows(), [])
  const [current, setCurrent] = useState<Workflow | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [kinds, setKinds] = useState<Record<string, NodeKind>>({})
  const [models, setModels] = useState<Record<string, string>>({})
  const [conditions, setConditions] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [workflowId, setWorkflowId] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const registry = useAsync(() => api.listModels({ active_only: true }), [])
  const modelIds = useMemo(
    () => Array.from(new Set((registry.data?.items ?? []).map((m) => m.model_id))),
    [registry.data],
  )

  const load = useCallback(
    (wf: Workflow) => {
      setCurrent(wf)
      setWorkflowId(wf.workflow_id)
      setName(wf.name)
      setNodes(toFlowNodes(wf))
      setEdges(toFlowEdges(wf))
      setKinds(Object.fromEntries(wf.nodes.map((n) => [n.node_id, n.kind])))
      setModels(Object.fromEntries(wf.nodes.map((n) => [n.node_id, n.model_id ?? ''])))
      setConditions(Object.fromEntries(wf.nodes.map((n) => [n.node_id, n.condition ?? ''])))
      setMessage(null)
      setError(null)
    },
    [setNodes, setEdges],
  )

  //  Open a workflow on arrival. A real graph says more than an empty canvas.
  const autoLoaded = useRef(false)
  useEffect(() => {
    if (autoLoaded.current) return
    const items = workflows.data?.items ?? []
    if (items.length === 0) return
    autoLoaded.current = true
    //  The largest one, since that is the one likely to have branches
    load([...items].sort((a, b) => b.nodes.length - a.nodes.length)[0])
  }, [workflows.data, load])

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, style: edgeStyle(null) }, eds)),
    [setEdges],
  )

  function addNode(kind: NodeKind) {
    const id = `${kind}-${nodes.length + 1}`
    setKinds((k) => ({ ...k, [id]: kind }))
    setNodes((ns) => [
      ...ns,
      {
        id,
        position: { x: 60 + (ns.length % 5) * 180, y: 120 + Math.floor(ns.length / 5) * 110 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: { label: label({ node_id: id, kind }) },
        style: nodeStyle(kind),
      },
    ])
  }

  async function save() {
    setError(null)
    setMessage(null)
    const body: Partial<Workflow> = {
      workflow_id: workflowId || `wf-${Date.now()}`,
      name: name || '이름 없는 워크플로',
      nodes: nodes.map((n) => ({
        node_id: n.id,
        kind: kinds[n.id] ?? 'model',
        label: null,
        model_id: models[n.id] || null,
        model_version: null,
        params: {},
        condition: conditions[n.id] || null,
        position: { x: n.position.x, y: n.position.y },
      })),
      edges: edges.map((e) => ({
        source: e.source,
        target: e.target,
        branch: (e.label === '참' ? 'true' : e.label === '거짓' ? 'false' : null) as
          | 'true'
          | 'false'
          | null,
      })),
      is_template: true,
    }

    try {
      const result = await api.saveWorkflow(body)
      const warn = result.unregistered_models.length
        ? ` (등록되지 않은 모델: ${result.unregistered_models.join(', ')})`
        : ''
      setMessage(`저장했습니다 — v${result.workflow.version} · 실행 층 ${result.levels.length}개${warn}`)
      workflows.reload()
    } catch (e) {
      //  Defects surface on save. Finding one at run time costs GPU hours.
      setError((e as Error).message)
    }
  }

  function setEdgeBranch(edgeId: string, branch: 'true' | 'false' | null) {
    setEdges((eds) =>
      eds.map((e) =>
        e.id === edgeId
          ? {
              ...e,
              label: branch === 'true' ? '참' : branch === 'false' ? '거짓' : undefined,
              style: edgeStyle(branch),
            }
          : e,
      ),
    )
  }

  const branchNodes = nodes.filter((n) => kinds[n.id] === 'branch')
  const modelNodes = nodes.filter((n) => (kinds[n.id] ?? 'model') === 'model')

  return (
    <>
      <PageHeader
        title="워크플로 스튜디오"
        description="노드를 조합해 실행 흐름을 설계합니다. 병렬 분기와 조건 분기를 지원하며 템플릿으로 저장해 재사용합니다."
        actions={
          <Button size="sm" onClick={save} disabled={nodes.length === 0}>
            <Save />
            저장
          </Button>
        }
      />
      <ErrorBox message={error} />
      <Notice message={message} />

      <Panel
        title="워크플로"
        description={`노드 ${nodes.length} · 간선 ${edges.length}`}
        actions={(['model', 'branch', 'fanout', 'join'] as NodeKind[]).map((k) => (
          <Button key={k} variant="outline" size="sm" onClick={() => addNode(k)}>
            <Plus />
            {KIND_LABEL[k]}
          </Button>
        ))}
      >
        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <Field>
            <Label htmlFor="load">불러오기</Label>
            <Select
              id="load"
              value={current?.workflow_id ?? ''}
              onChange={(e) => {
                const wf = workflows.data?.items.find((w) => w.workflow_id === e.target.value)
                if (wf) load(wf)
              }}
            >
              <option value="">— 선택 —</option>
              {workflows.data?.items.map((w) => (
                <option key={w.workflow_id} value={w.workflow_id}>
                  {w.name} (v{w.version})
                </option>
              ))}
            </Select>
          </Field>
          <Field>
            <Label htmlFor="wfid">식별자</Label>
            <Input
              id="wfid"
              value={workflowId}
              onChange={(e) => setWorkflowId(e.target.value)}
              placeholder="wf-binding"
              className="font-mono text-[13px]"
            />
          </Field>
          <Field>
            <Label htmlFor="wfname">이름</Label>
            <Input
              id="wfname"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="결합 예측"
            />
          </Field>
        </div>

        <div className="h-[460px] overflow-hidden rounded-md border">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background color="var(--border)" gap={20} />
            <Controls />
            <MiniMap pannable zoomable maskColor="var(--muted)" nodeColor="var(--muted-foreground)" />
          </ReactFlow>
        </div>

        <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px]">
          <span className="flex items-center gap-2">
            <span className="bg-foreground inline-block h-3.5 w-7 rounded-[3px]" />
            모델
          </span>
          <span className="flex items-center gap-2">
            <span className="bg-muted inline-block h-3.5 w-7 rounded-[3px] border" />
            변환
          </span>
          <span className="flex items-center gap-2">
            <span className="border-foreground inline-block h-3.5 w-7 rounded-full border-2" />
            조건 분기
          </span>
          <span className="flex items-center gap-2">
            <span className="border-foreground inline-block h-3.5 w-7 rounded-[3px] border border-dashed" />
            병렬 분기
          </span>
          <span className="flex items-center gap-2">
            <span className="border-foreground inline-block h-3.5 w-7 rounded-[3px] border" />
            합류
          </span>
          <span>간선 — 실선 참 · 점선 거짓</span>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="모델 노드" bodyClassName={modelNodes.length ? 'p-0' : undefined}>
          {modelNodes.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">노드</TableHead>
                  <TableHead>실행할 모델</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {modelNodes.map((n) => (
                  <TableRow key={n.id}>
                    <TableCell className="font-mono text-[12.5px]">{n.id}</TableCell>
                    <TableCell>
                      <Select
                        value={models[n.id] ?? ''}
                        onChange={(e) => setModels((m) => ({ ...m, [n.id]: e.target.value }))}
                      >
                        <option value="">— 미지정 —</option>
                        {modelIds.map((id) => (
                          <option key={id} value={id}>
                            {id}
                          </option>
                        ))}
                      </Select>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty>모델 노드를 추가하십시오.</Empty>
          )}
        </Panel>

        <Panel title="조건 분기">
          {branchNodes.length ? (
            <div className="flex flex-col gap-3.5">
              <p className="text-muted-foreground text-[12.5px]">
                <code className="font-mono">노드.지표 &gt; 값</code> 형태로 씁니다. 예 —{' '}
                <code className="font-mono">soluprot.pass_rate &gt; 0.3</code>
              </p>
              {branchNodes.map((n) => (
                <Field key={n.id}>
                  <Label htmlFor={`cond-${n.id}`}>{n.id}</Label>
                  <Input
                    id={`cond-${n.id}`}
                    value={conditions[n.id] ?? ''}
                    onChange={(e) => setConditions((c) => ({ ...c, [n.id]: e.target.value }))}
                    placeholder="soluprot.pass_rate > 0.3"
                    className="font-mono text-[13px]"
                  />
                </Field>
              ))}
              <h4 className="text-[13px] font-semibold">분기에서 나가는 간선</h4>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>간선</TableHead>
                    <TableHead className="w-[150px]">가지</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {edges
                    .filter((e) => kinds[e.source] === 'branch')
                    .map((e) => (
                      <TableRow key={e.id}>
                        <TableCell className="font-mono text-[12.5px]">
                          {e.source} → {e.target}
                        </TableCell>
                        <TableCell>
                          <Select
                            value={e.label === '참' ? 'true' : e.label === '거짓' ? 'false' : ''}
                            onChange={(ev) =>
                              setEdgeBranch(e.id, (ev.target.value || null) as 'true' | 'false' | null)
                            }
                          >
                            <option value="">— 미지정 —</option>
                            <option value="true">참</option>
                            <option value="false">거짓</option>
                          </Select>
                        </TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <Empty>조건 분기 노드를 추가하면 조건식을 지정합니다.</Empty>
          )}
        </Panel>
      </div>
    </>
  )
}
