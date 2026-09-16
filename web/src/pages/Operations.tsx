import { useState } from 'react'
import { Eraser, RotateCcw, Stethoscope } from 'lucide-react'

import { api } from '../api/client'
import { usePolling, useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel, Stat, formatTime } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useIdentity } from '@/lib/identity'

/** Operations: the job queue and the audit trail. */
export function Operations() {
  const jobs = usePolling(() => api.jobStats(), 5000, [])
  const audit = useAsync(() => api.listAudit({ limit: 100 }), [])
  const health = useAsync(() => api.health(), [])
  const { canAdmin } = useIdentity()
  const [message, setMessage] = useState<string | null>(null)

  async function reclaim() {
    const { reclaimed } = await api.reclaimJobs()
    setMessage(`만료된 lease ${reclaimed}건을 큐로 되돌렸습니다.`)
    jobs.reload()
  }

  async function prune() {
    setMessage(null)
    try {
      const r = await api.pruneInputs()
      setMessage(
        r.removed
          ? `${r.days}일 넘게 어느 실행도 읽지 않은 파일 ${r.removed}개를 지웠습니다 (${Math.round(r.freed_bytes / 1024)} KB).`
          : `${r.days}일 넘게 읽히지 않은 파일이 없습니다.`,
      )
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  /** Reclaiming works on jobs alone. This is the other half: it tells the
      runs those jobs belonged to, and re-queues work that went missing. */
  async function reconcile() {
    setMessage(null)
    try {
      const report = await api.reconcileRuns()
      setMessage(
        report.repaired.length
          ? `진행 중인 실행 ${report.checked}건을 점검해 ${report.repaired.length}건을 고쳤습니다 — ${report.repaired
              .map((r) => r.run_id)
              .join(', ')}`
          : `진행 중인 실행 ${report.checked}건 모두 작업과 맞습니다.`,
      )
      jobs.reload()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  const by = jobs.data?.by_status ?? {}

  return (
    <>
      <PageHeader
        title="운영"
        description="작업 큐 상태와 감사 로그를 확인합니다."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={reconcile} disabled={!canAdmin}>
              <Stethoscope />
              정합성 점검
            </Button>
            <Button variant="outline" size="sm" onClick={reclaim} disabled={!canAdmin}>
              <RotateCcw />
              만료 회수
            </Button>
            <Button variant="outline" size="sm" onClick={prune} disabled={!canAdmin}>
              <Eraser />
              입력 파일 정리
            </Button>
          </>
        }
      />
      <ErrorBox message={jobs.error ?? audit.error} />
      <Notice message={message} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          status={health.data?.status === 'ok' ? 'succeeded' : 'failed'}
          label="시스템"
          value={health.data?.status === 'ok' ? '정상' : '점검'}
          caption="/healthz"
        />
        <Stat
          status={health.data?.mongo_ok ? 'succeeded' : 'failed'}
          label="데이터 저장소"
          value={health.data?.mongo_ok ? '연결' : '끊김'}
          caption="MongoDB"
        />
        <Stat status="pending" label="큐 대기" value={by.queued ?? 0} caption="점유 대기" />
        <Stat status="running" label="점유 중" value={by.leased ?? 0} caption="워커 처리 중" />
      </div>

      <Panel title="감사 로그" description={`${audit.data?.count ?? 0}건`} bodyClassName="p-0">
        {audit.data?.items.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[150px]">시각</TableHead>
                <TableHead className="w-[200px]">행위</TableHead>
                <TableHead className="w-[160px]">주체</TableHead>
                <TableHead>대상</TableHead>
                <TableHead className="w-[110px]">결과</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.data.items.map((a, i) => (
                <TableRow key={i}>
                  <TableCell className="tabular text-muted-foreground text-[12.5px]">
                    {formatTime(a.created_at as string)}
                  </TableCell>
                  <TableCell className="font-mono text-[12.5px]">{String(a.action)}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {(a.actor_id as string) ?? '—'}
                  </TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[12.5px]">
                    {(a.target_id as string) ?? '—'}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{String(a.result)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty>기록이 없습니다.</Empty>
        )}
      </Panel>
    </>
  )
}
