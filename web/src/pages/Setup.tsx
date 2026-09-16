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
import { useIdentity } from '@/lib/identity'
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
        request: {
          target_fasta: targetFasta,
          target_pdb: targetPdb,
          design_chains: chains
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean),
        },
      })
      setMessage(`실행을 시작했습니다 — ${run.run_id}`)
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
        </Panel>

        <Panel title="입력">
          <div className="flex flex-col gap-3.5">
            <Field>
              <Label htmlFor="fasta">대상 서열 (FASTA 경로)</Label>
              <Input
                id="fasta"
                value={targetFasta}
                onChange={(e) => setTargetFasta(e.target.value)}
                placeholder="/data/targets/lysozyme.fasta"
                className="font-mono text-[13px]"
              />
            </Field>
            <Field>
              <Label htmlFor="pdb">대상 구조 (PDB 경로)</Label>
              <Input
                id="pdb"
                value={targetPdb}
                onChange={(e) => setTargetPdb(e.target.value)}
                placeholder="/data/targets/lysozyme.pdb"
                className="font-mono text-[13px]"
              />
            </Field>
            <Field>
              <Label htmlFor="chains">설계 체인 (쉼표로 구분)</Label>
              <Input id="chains" value={chains} onChange={(e) => setChains(e.target.value)} />
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
