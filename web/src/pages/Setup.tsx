import { useEffect, useState } from 'react'
import { CheckCircle2, Play } from 'lucide-react'

import { api } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel, formatBytes } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { InputField } from '../components/InputField'
import { ResiduePicker, type FixedPositions } from '../components/ResiduePicker'
import type { PdbSummary } from '@/lib/bio'
import { useIdentity } from '@/lib/identity'
import { roundLabel, useProject } from '@/lib/project'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/** Choose a workflow, attach inputs, start a run. */
export function Setup() {
  const { current, rounds, filter } = useProject()
  //  Scoped the way the dashboard is: a workflow another project owns must
  //  not be offered here, where it would be started under this one.
  const workflows = useAsync(() => api.listWorkflows({ project_id: filter }), [filter])
  const [workflowId, setWorkflowId] = useState('')
  const [targetFasta, setTargetFasta] = useState('')
  const [targetPdb, setTargetPdb] = useState('')
  const [chains, setChains] = useState('A')
  //  A parseable structure lets the researcher pick residues to hold fixed in
  //  3D. Kept here so the picker shows only when there is a structure to draw.
  const [pdbSummary, setPdbSummary] = useState<PdbSummary | null>(null)
  const [fixed, setFixed] = useState<FixedPositions>({})
  const [preflight, setPreflight] = useState<Awaited<ReturnType<typeof api.preflight>> | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { canRun } = useIdentity()
  //  Re-read after every upload - the field says when one landed - and not
  //  on keystrokes, which change nothing stored.
  const [uploads, setUploads] = useState(0)
  const usage = useAsync(() => api.inputUsage(), [uploads])
  //  null: nothing chosen yet, so the latest round. '': chosen to be none.
  //  Two different things, and `roundId || latest` could not tell them apart -
  //  「회차 없이」 snapped straight back to the latest round.
  const [roundId, setRoundId] = useState<string | null>(null)
  //  Once the chains were edited by hand, a dropped structure stops
  //  overwriting them. The autofill is a default, not a rule.
  const [chainsTouched, setChainsTouched] = useState(false)

  //  A round belongs to its project. Switching projects with a round chosen
  //  would otherwise file the run under a round of the project just left.
  useEffect(() => {
    setRoundId(null)
  }, [current?.project_id])

  const latest = rounds.length ? rounds[rounds.length - 1].round_id : ''
  const round = roundId === null ? latest : roundId

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
          //  Trimmed, so a path with Enter after it is the path.
          target_fasta: targetFasta.trim(),
          target_pdb: targetPdb.trim(),
          design_chains: chains
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean),
          //  Only sent when residues were actually picked; an empty map would
          //  read at the endpoint as "hold nothing", which is already the default.
          ...(Object.keys(fixed).length ? { fixed_positions: fixed } : {}),
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
              onUploaded={() => setUploads((n) => n + 1)}
              placeholder={'>lysozyme\nMKALIVLGLVLLSVTVQGKVFERCELARTLKRLGMDGYRG…'}
              disabled={!canRun}
            />
            <InputField
              id="pdb"
              kind="pdb"
              label="대상 구조"
              value={targetPdb}
              onChange={(v) => {
                setTargetPdb(v)
                //  Residue numbers belong to one structure. Swap the structure
                //  and any picks made against the old one no longer mean the
                //  same residue, so they are cleared.
                setFixed({})
              }}
              onUploaded={() => setUploads((n) => n + 1)}
              onSummary={(s) => {
                //  A structure says which chains it has. Filling them in
                //  saves typing and, more to the point, saves typing a chain
                //  the file does not contain.
                const p = s as PdbSummary | null
                setPdbSummary(p)
                if (!chainsTouched && p?.ok && !p.cif && p.chains.length) {
                  setChains(p.chains.join(', '))
                }
              }}
              placeholder="/data/targets/lysozyme.pdb 또는 PDB 내용"
              disabled={!canRun}
            />
            {usage.data && (
              <p className="text-muted-foreground text-[11.5px]">
                올린 파일 {formatBytes(usage.data.used_bytes)} / {formatBytes(usage.data.quota_bytes)}.
                실행에 쓰이지 않은 파일은 {usage.data.retention_days}일 뒤 지워집니다.
              </p>
            )}
            <Field>
              <Label htmlFor="chains">설계 체인 (쉼표로 구분)</Label>
              <Input
                id="chains"
                value={chains}
                onChange={(e) => {
                  setChains(e.target.value)
                  setChainsTouched(true)
                }}
                disabled={!canRun}
              />
            </Field>

            {/*  A structure that parsed to real chains can be shown in 3D to
                pick residues to hold fixed. mmCIF is not drawn here yet, and a
                path (not inline text) has no atoms to render. */}
            {targetPdb.trim() && pdbSummary?.ok && !pdbSummary.cif ? (
              <Field>
                <Label>고정할 잔기 (선택)</Label>
                <ResiduePicker
                  pdb={targetPdb}
                  value={fixed}
                  onChange={setFixed}
                  disabled={!canRun}
                />
              </Field>
            ) : null}
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
