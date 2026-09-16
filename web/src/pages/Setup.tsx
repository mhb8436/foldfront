import { useState } from 'react'
import { CheckCircle2, Play } from 'lucide-react'

import { api } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { InputField } from '../components/InputField'
import type { PdbSummary } from '@/lib/bio'
import { useIdentity } from '@/lib/identity'
import { roundLabel, useProject } from '@/lib/project'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/** Choose a workflow, attach inputs, start a run. */
export function Setup() {
  const workflows = useAsync(() => api.listWorkflows(), [])
  const [workflowId, setWorkflowId] = useState('')
  const [targetFasta, setTargetFasta] = useState('')
  const [targetPdb, setTargetPdb] = useState('')
  const [chains, setChains] = useState('A')
  const [preflight, setPreflight] = useState<Awaited<ReturnType<typeof api.preflight>> | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { canRun } = useIdentity()
  const { current, rounds } = useProject()
  const [roundId, setRoundId] = useState('')

  //  The latest round, because a redesign loop runs in the one just opened.
  //  Explicit still wins: once a round is picked here, it stays picked.
  const latest = rounds.length ? rounds[rounds.length - 1].round_id : ''
  const round = roundId || latest

  const selected = workflowId || workflows.data?.items[0]?.workflow_id || ''

  async function runPreflight() {
    setError(null)
    setBusy(true)
    try {
      setPreflight(await api.preflight(selected))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function start() {
    setError(null)
    setMessage(null)
    setBusy(true)
    try {
      const run = await api.startRun({
        workflow_id: selected,
        //  Without these the run belongs to nothing and never appears in a
        //  project's view of its own work.
        project_id: current?.project_id,
        round_id: current && round ? round : undefined,
        request: {
          target_fasta: targetFasta,
          target_pdb: targetPdb,
          design_chains: chains
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean),
        },
      })
      setMessage(
        current
          ? `실행을 시작했습니다 — ${run.run_id} · ${current.name}${
              round ? ` · ${rounds.find((r) => r.round_id === round)?.index ?? '?'}차` : ''
            }`
          : `실행을 시작했습니다 — ${run.run_id}`,
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function seed() {
    await api.seedBuiltin()
    workflows.reload()
  }

  return (
    <>
      <PageHeader title="실행 준비" description="워크플로를 고르고 입력을 붙여 실행합니다." />
      <ErrorBox message={error ?? workflows.error} />
      <Notice message={message} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="워크플로"
          actions={
            <Button variant="outline" size="sm" onClick={seed} disabled={!canRun}>
              기본 템플릿 등록
            </Button>
          }
        >
          {workflows.data?.items.length ? (
            <Field>
              <Label htmlFor="workflow">실행할 워크플로</Label>
              <Select
                id="workflow"
                value={selected}
                onChange={(e) => {
                  setWorkflowId(e.target.value)
                  setPreflight(null)
                }}
              >
                {workflows.data.items.map((w) => (
                  <option key={w.workflow_id} value={w.workflow_id}>
                    {w.name} (v{w.version} · 노드 {w.nodes.length})
                  </option>
                ))}
              </Select>
            </Field>
          ) : (
            <Empty>등록된 워크플로가 없습니다. 기본 템플릿을 먼저 등록하십시오.</Empty>
          )}

          {/*  Where the result will be filed. Said here rather than found out
              afterwards on a screen that does not list the run. */}
          <div className="mt-3.5">
            {current ? (
              rounds.length ? (
                <Field>
                  <Label htmlFor="round">기록할 회차</Label>
                  <Select id="round" value={round} onChange={(e) => setRoundId(e.target.value)}>
                    <option value="">— 회차 없이 {current.name}에 기록 —</option>
                    {rounds.map((r) => (
                      <option key={r.round_id} value={r.round_id}>
                        {roundLabel(r)}
                        {r.objective ? ` — ${r.objective}` : ''}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : (
                <p className="text-muted-foreground text-[12.5px]">
                  <span className="text-foreground font-medium">{current.name}</span>에 기록합니다.
                  회차는 「프로젝트」 화면에서 엽니다.
                </p>
              )
            ) : (
              <p className="text-muted-foreground text-[12.5px]">
                프로젝트를 고르지 않아 어느 프로젝트에도 기록되지 않습니다. 머리말의 프로젝트
                선택기에서 고르십시오.
              </p>
            )}
          </div>
        </Panel>

        <Panel title="입력">
          <div className="flex flex-col gap-3.5">
            <InputField
              id="fasta"
              kind="fasta"
              label="대상 서열"
              value={targetFasta}
              onChange={setTargetFasta}
              placeholder={'>lysozyme\nMKALIVLGLVLLSVTVQGKVFERCELARTLKRLGMDGYRG…'}
              disabled={!canRun}
            />
            <InputField
              id="pdb"
              kind="pdb"
              label="대상 구조"
              value={targetPdb}
              onChange={setTargetPdb}
              onSummary={(s) => {
                //  A structure says which chains it has. Filling them in
                //  saves typing and, more to the point, saves typing a chain
                //  the file does not contain.
                const p = s as PdbSummary | null
                if (p?.ok && p.chains.length) setChains(p.chains.join(', '))
              }}
              placeholder="/data/targets/lysozyme.pdb 또는 PDB 내용"
              disabled={!canRun}
            />
            <Field>
              <Label htmlFor="chains">설계 체인 (쉼표로 구분)</Label>
              <Input
                id="chains"
                value={chains}
                onChange={(e) => setChains(e.target.value)}
                disabled={!canRun}
              />
            </Field>
          </div>
        </Panel>
      </div>

      <Panel
        title="실행 전 점검"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={runPreflight} disabled={!selected || busy}>
              <CheckCircle2 />
              점검
            </Button>
            <Button size="sm" onClick={start} disabled={!selected || busy || !canRun}>
              <Play />
              실행
            </Button>
          </>
        }
      >
        {preflight ? (
          <div className="flex flex-col gap-3">
            <p className="text-muted-foreground text-[13px]">
              {preflight.ok
                ? `모든 모델을 해석했습니다 — 노드 ${preflight.node_count}개 · GPU ${preflight.total_gpu}개 필요`
                : '실행할 수 없습니다.'}
            </p>
            <ErrorBox message={preflight.graph_error} />
            {preflight.errors.map((e) => (
              <ErrorBox key={e} message={e} />
            ))}
            {preflight.levels && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[80px]">층</TableHead>
                    <TableHead>동시에 실행되는 노드</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preflight.levels.map((layer, i) => (
                    <TableRow key={i}>
                      <TableCell className="tabular text-muted-foreground">{i + 1}</TableCell>
                      <TableCell className="font-mono text-[12.5px]">{layer.join(' · ')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        ) : (
          <Empty>점검을 누르면 그래프와 모델 해석 결과를 확인합니다.</Empty>
        )}
      </Panel>
    </>
  )
}
