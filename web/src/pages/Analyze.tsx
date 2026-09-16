import { useEffect, useState } from 'react'

import { useProject } from '@/lib/project'
import { api, type Artifact, type Run } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Panel, StatusBadge, duration } from '../components/Common'
import { StructureViewer } from '../components/StructureViewer'
import { metricTerm, stageTerm } from '@/lib/glossary'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/** Comparison between runs, and ranking of the candidates they produced. */
export function Analyze() {
  const { filter } = useProject()
  const runs = useAsync(() => api.listRuns({ project_id: filter, limit: 100 }), [filter])
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')

  const items = runs.data?.items ?? []

  //  Arriving at two empty selectors means choosing twice, every visit.
  //  Preselect the two most recent finished runs, which is usually what
  //  someone comparing a fresh run against its predecessor wants.
  useEffect(() => {
    if (left || right || items.length === 0) return
    const done = items.filter((r) => r.status === 'succeeded')
    if (done.length >= 2) {
      setLeft(done[1].run_id)
      setRight(done[0].run_id)
    }
  }, [items, left, right])

  const a = items.find((r) => r.run_id === left)
  const b = items.find((r) => r.run_id === right)

  return (
    <>
      <PageHeader title="결과 분석" description="실행 결과를 비교하고 후보군을 추립니다." />
      <ErrorBox message={runs.error} />

      <Panel title="비교 대상">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <Label htmlFor="left">좌</Label>
            <Select id="left" value={left} onChange={(e) => setLeft(e.target.value)}>
              <option value="">— 선택 —</option>
              {items.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} ({r.status})
                </option>
              ))}
            </Select>
          </Field>
          <Field>
            <Label htmlFor="right">우</Label>
            <Select id="right" value={right} onChange={(e) => setRight(e.target.value)}>
              <option value="">— 선택 —</option>
              {items.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} ({r.status})
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Panel>

      <Panel title="구조 비교">
        {a && b ? <Structures a={a} b={b} /> : <Empty>실행 둘을 고르면 구조를 나란히 놓습니다.</Empty>}
      </Panel>

      <Panel title="run 대 run 비교" bodyClassName={a && b ? 'p-0' : undefined}>
        {a && b ? <Compare a={a} b={b} /> : <Empty>비교할 실행 둘을 고르십시오.</Empty>}
      </Panel>

      <Panel title="단계별 지표 요약" bodyClassName={items.length ? 'p-0' : undefined}>
        {items.length ? <MetricTable runs={items.slice(0, 20)} /> : <Empty>실행 기록이 없습니다.</Empty>}
      </Panel>
    </>
  )
}

function Compare({ a, b }: { a: Run; b: Run }) {
  const names = Array.from(new Set([...a.stages.map((s) => s.name), ...b.stages.map((s) => s.name)]))
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[150px]">단계</TableHead>
          <TableHead className="font-mono whitespace-nowrap">{a.run_id}</TableHead>
          <TableHead className="font-mono whitespace-nowrap">{b.run_id}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell className="font-semibold">전체</TableCell>
          <TableCell>
            <div className="flex items-center gap-2">
              <StatusBadge status={a.status} />
              <span className="text-muted-foreground tabular text-[12.5px]">
                {duration(a.started_at, a.finished_at)}
              </span>
            </div>
          </TableCell>
          <TableCell>
            <div className="flex items-center gap-2">
              <StatusBadge status={b.status} />
              <span className="text-muted-foreground tabular text-[12.5px]">
                {duration(b.started_at, b.finished_at)}
              </span>
            </div>
          </TableCell>
        </TableRow>
        {names.map((name) => {
          const sa = a.stages.find((s) => s.name === name)
          const sb = b.stages.find((s) => s.name === name)
          return (
            <TableRow key={name}>
              <TableCell>
                <div>{name}</div>
                {stageTerm(name) && (
                  <div className="text-muted-foreground text-[11.5px]" title={stageTerm(name)!.hint}>
                    {stageTerm(name)!.label}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <Cell metrics={sa?.metrics} status={sa?.status} />
              </TableCell>
              <TableCell>
                <Cell metrics={sb?.metrics} status={sb?.status} />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Cell({ metrics, status }: { metrics?: Record<string, unknown>; status?: string }) {
  if (!status) return <span className="text-muted-foreground">—</span>
  const shown = Object.entries(metrics ?? {}).filter(([k]) => !k.startsWith('_'))
  return (
    <>
      <StatusBadge status={status} />
      {shown.length > 0 && (
        <div className="text-muted-foreground mt-1 font-mono text-[12.5px]">
          {shown.map(([k, v]) => `${k}=${v}`).join(' · ')}
        </div>
      )}
    </>
  )
}

function MetricTable({ runs }: { runs: Run[] }) {
  //  Runs may have different stages, so the columns are gathered from the
  //  metric names that actually appear rather than fixed in advance
  const keys = Array.from(
    new Set(
      runs.flatMap((r) =>
        r.stages.flatMap((s) =>
          Object.keys(s.metrics ?? {})
            .filter((k) => !k.startsWith('_'))
            .map((k) => `${s.name}.${k}`),
        ),
      ),
    ),
  ).slice(0, 8)

  if (keys.length === 0) return <Empty>기록된 지표가 없습니다.</Empty>

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[200px]">실행</TableHead>
          <TableHead className="w-[110px]">상태</TableHead>
          {keys.map((k) => (
            <TableHead key={k} className="font-mono" title={metricTerm(k)?.hint}>
              <div>{k}</div>
              {metricTerm(k) && (
                <div className="font-sans text-[11px] font-normal">{metricTerm(k)!.label}</div>
              )}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r) => (
          <TableRow key={r.run_id}>
            <TableCell className="font-mono text-[12.5px] whitespace-nowrap">{r.run_id}</TableCell>
            <TableCell>
              <StatusBadge status={r.status} />
            </TableCell>
            {keys.map((k) => {
              const [stage, metric] = k.split('.')
              const v = r.stages.find((s) => s.name === stage)?.metrics?.[metric]
              return (
                <TableCell key={k} className="tabular">
                  {v === undefined ? <span className="text-muted-foreground">—</span> : String(v)}
                </TableCell>
              )
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Designed structures, placed side by side. */
function Structures({ a, b }: { a: Run; b: Run }) {
  const left = useAsync(() => api.listArtifacts(a.run_id), [a.run_id])
  const right = useAsync(() => api.listArtifacts(b.run_id), [b.run_id])

  //  The first structure opens; the rest are chosen from run monitoring
  const pick = (items?: Artifact[]) => items?.find((x) => x.kind === 'pdb')
  const pa = pick(left.data?.items)
  const pb = pick(right.data?.items)

  if (left.loading || right.loading) return <Empty>구조를 찾는 중입니다.</Empty>
  if (!pa && !pb) return <Empty>두 실행 모두 구조 산출물이 없습니다.</Empty>

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {[
        { run: a, art: pa },
        { run: b, art: pb },
      ].map(({ run, art }) => (
        <div key={run.run_id} className="flex flex-col gap-1.5">
          <div className="font-mono text-[12.5px] font-medium">{run.run_id}</div>
          {art ? (
            <StructureViewer runId={run.run_id} path={art.path} label={art.path} height={360} />
          ) : (
            <div className="text-muted-foreground flex h-[360px] items-center justify-center rounded-md border text-[12.5px]">
              구조 산출물이 없습니다
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
