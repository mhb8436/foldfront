import { useState } from 'react'
import { Download, RefreshCw, X } from 'lucide-react'

import { api } from '../api/client'
import { StructureViewer } from '../components/StructureViewer'
import { metricTerm, stageTerm } from '@/lib/glossary'
import { usePolling, useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import {
  Empty,
  ErrorBox,
  Panel,
  StageProgress,
  Stat,
  StatusBadge,
  duration,
  formatBytes,
  formatTime,
} from '../components/Common'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/** Run status, artifacts and the event log. */
export function Monitor() {
  const [selected, setSelected] = useState<string | null>(null)
  const runs = usePolling(() => api.listRuns({ limit: 50 }), 4000, [])
  const jobs = usePolling(() => api.jobStats(), 4000, [])

  const by = jobs.data?.by_status ?? {}

  return (
    <>
      <PageHeader
        title="실행 감시"
        description="실행 상태와 산출물을 확인합니다. 4초마다 갱신됩니다."
        actions={
          <Button variant="outline" size="sm" onClick={() => runs.reload()}>
            <RefreshCw />
            새로고침
          </Button>
        }
      />
      <ErrorBox message={runs.error} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat status="pending" label="대기" value={by.queued ?? 0} caption="큐에서 점유 대기" />
        <Stat status="running" label="진행" value={by.leased ?? 0} caption="워커가 점유 중" />
        <Stat status="succeeded" label="완료" value={by.succeeded ?? 0} caption="누적" />
        <Stat status="failed" label="실패" value={by.failed ?? 0} caption="재시도 한도 초과" />
        <Stat label="전체" value={jobs.data?.total ?? 0} caption="작업 큐 누적" />
      </div>

      <Panel
        title="실행 목록"
        description={`${runs.data?.count ?? 0}건`}
        bodyClassName="p-0"
      >
        {runs.data?.items.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[200px]">실행 식별자</TableHead>
                <TableHead className="w-[100px]">상태</TableHead>
                <TableHead>워크플로</TableHead>
                <TableHead className="w-[190px]">단계 진척</TableHead>
                <TableHead className="w-[130px]">개시 시각</TableHead>
                <TableHead className="w-[110px]">소요</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data.items.map((r) => (
                <TableRow
                  key={r.run_id}
                  onClick={() => setSelected(r.run_id)}
                  data-state={selected === r.run_id ? 'selected' : undefined}
                  className="cursor-pointer"
                >
                  <TableCell className="font-mono text-[12.5px]">{r.run_id}</TableCell>
                  <TableCell>
                    <StatusBadge status={r.status} />
                  </TableCell>
                  <TableCell>{r.workflow_id ?? '—'}</TableCell>
                  <TableCell>
                    <StageProgress stages={r.stages} />
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                    {formatTime(r.started_at ?? r.created_at)}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                    {duration(r.started_at, r.finished_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty>실행 기록이 없습니다.</Empty>
        )}
      </Panel>

      {selected && <RunDetail runId={selected} onClose={() => setSelected(null)} />}
    </>
  )
}

function RunDetail({ runId, onClose }: { runId: string; onClose: () => void }) {
  const run = usePolling(() => api.getRun(runId), 3000, [runId])
  const events = usePolling(() => api.listEvents(runId), 3000, [runId])
  const artifacts = useAsync(() => api.listArtifacts(runId), [runId])
  const [structure, setStructure] = useState<string | null>(null)

  async function cancel() {
    await api.cancelRun(runId, '화면에서 취소')
    run.reload()
  }

  async function fork(stage: string) {
    await api.forkRun(runId, stage)
    onClose()
  }

  return (
    <Panel
      title={`실행 상세 — ${runId}`}
      actions={
        <>
          {run.data?.status === 'running' && (
            <Button variant="outline" size="sm" onClick={cancel}>
              취소
            </Button>
          )}
          <Button variant="ghost" size="icon" className="size-8" aria-label="닫기" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </>
      }
      bodyClassName="flex flex-col gap-5 p-0"
    >
      <div className="px-4 pt-4">
        <ErrorBox message={run.error} />
        {run.data?.forked_from_run_id && (
          <p className="text-muted-foreground text-[13px]">
            <code className="font-mono">{run.data.forked_from_run_id}</code> 의{' '}
            <code className="font-mono">{run.data.forked_from_stage}</code> 단계에서 갈라진
            실행입니다.
          </p>
        )}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[150px]">단계</TableHead>
            <TableHead className="w-[110px]">상태</TableHead>
            <TableHead className="w-[220px]">모델</TableHead>
            <TableHead>지표</TableHead>
            <TableHead className="w-[120px]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.data?.stages.map((s) => (
            <TableRow key={s.name}>
              <TableCell>
                <div className="font-medium">{s.name}</div>
                {stageTerm(s.name) && (
                  <div className="text-muted-foreground text-[11.5px]" title={stageTerm(s.name)!.hint}>
                    {stageTerm(s.name)!.label}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <StatusBadge status={s.status} />
                {s.error && (
                  <div className="text-destructive mt-1 text-[12px]">{s.error}</div>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground font-mono text-[12.5px]">
                {s.model_id ? `${s.model_id}:${s.model_version ?? '기본'}` : '—'}
              </TableCell>
              <TableCell className="text-muted-foreground font-mono text-[12.5px]">
                {Object.entries(s.metrics).filter(([k]) => !k.startsWith('_')).length === 0
                  ? '—'
                  : Object.entries(s.metrics)
                      .filter(([k]) => !k.startsWith('_'))
                      .map(([k, v]) => (
                        <span key={k} className="mr-3 inline-block" title={metricTerm(k)?.hint}>
                          {k}={String(v)}
                        </span>
                      ))}
              </TableCell>
              <TableCell>
                <Button variant="outline" size="sm" onClick={() => fork(s.name)}>
                  여기서 fork
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <div className="border-t px-4 pt-4">
        <h3 className="mb-2 text-[13.5px] font-semibold">
          산출물{' '}
          <span className="text-muted-foreground font-normal">
            {artifacts.data?.count ?? 0}건 · {formatBytes(artifacts.data?.total_bytes ?? 0)}
          </span>
        </h3>
      </div>
      {artifacts.data?.items.length ? (
        <>
          {structure && (
            <div className="px-4 pb-4">
              <StructureViewer runId={runId} path={structure} label={structure} height={380} />
            </div>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>경로</TableHead>
                <TableHead className="w-[130px]">단계</TableHead>
                <TableHead className="w-[130px]">종류</TableHead>
                <TableHead className="w-[110px]">크기</TableHead>
                <TableHead className="w-[170px]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {artifacts.data.items.slice(0, 40).map((a) => (
                <TableRow key={a.path} data-state={structure === a.path ? 'selected' : undefined}>
                  <TableCell className="font-mono text-[12.5px]">{a.path}</TableCell>
                  <TableCell className="text-muted-foreground">{a.stage ?? '—'}</TableCell>
                  <TableCell className="text-muted-foreground">{a.kind}</TableCell>
                  <TableCell className="tabular text-muted-foreground">
                    {formatBytes(a.size_bytes)}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {a.kind === 'pdb' && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setStructure(structure === a.path ? null : a.path)}
                        >
                          {structure === a.path ? '닫기' : '구조 열람'}
                        </Button>
                      )}
                      <Button variant="ghost" size="sm" asChild>
                        <a href={api.artifactUrl(runId, a.path)} download aria-label="내려받기">
                          <Download />
                        </a>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      ) : (
        <Empty>산출물이 없습니다.</Empty>
      )}

      <div className="border-t px-4 py-4">
        <h3 className="mb-2.5 text-[13.5px] font-semibold">이벤트</h3>
        <div className="max-h-[340px] overflow-y-auto">
          {events.data?.items.map((e) => (
            <div
              key={e.seq}
              className={`border-border/60 flex gap-2 border-b py-1.5 text-[12.5px] last:border-0 ${
                e.level === 'error'
                  ? 'text-destructive'
                  : e.level === 'warning'
                    ? 'text-foreground'
                    : 'text-muted-foreground'
              }`}
            >
              <span className="tabular shrink-0 font-mono">{formatTime(e.created_at)}</span>
              {e.stage && <code className="shrink-0 font-mono">{e.stage}</code>}
              <span className="min-w-0">{e.message}</span>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  )
}
