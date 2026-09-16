import { Link } from 'react-router-dom'
import { ArrowRight, Play, RefreshCw } from 'lucide-react'

import { api } from '../api/client'
import { usePolling } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import {
  Empty,
  ErrorBox,
  Panel,
  StageProgress,
  Stat,
  StatusBadge,
  StatusDot,
  duration,
  formatTime,
} from '../components/Common'
import { stageTerm } from '@/lib/glossary'
import { useIdentity } from '@/lib/identity'
import { useProject } from '@/lib/project'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/**
 * The landing screen.
 *
 * Someone opening the console wants to know whether anything is running, what
 * finished, and whether anything needs them. Everything here is a starting
 * point for a screen that goes deeper, so nothing is duplicated - the numbers
 * link to the view that owns them.
 */
export function Dashboard() {
  const { current, filter } = useProject()
  const summary = usePolling(() => api.summary(6, filter), 8000, [filter])
  const s = summary.data
  const { canRun } = useIdentity()

  const byStatus = s?.runs.by_status ?? {}
  const succeeded = byStatus.succeeded ?? 0
  const finished = succeeded + (byStatus.failed ?? 0) + (byStatus.cancelled ?? 0)
  const rate = finished ? Math.round((succeeded / finished) * 100) : null

  return (
    <>
      <PageHeader
        title="대시보드"
        description={
          current
            ? `${current.name}의 실행과 워크플로입니다. 8초마다 갱신됩니다.`
            : '지금 무엇이 돌고 있고 무엇이 손을 기다리는지 봅니다. 8초마다 갱신됩니다.'
        }
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => summary.reload()}>
              <RefreshCw />
              새로고침
            </Button>
            {canRun && (
              <Button size="sm" asChild>
                <Link to="/setup">
                  <Play />
                  실행 시작
                </Link>
              </Button>
            )}
          </>
        }
      />
      <ErrorBox message={summary.error} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          status="running"
          label="진행 중"
          value={byStatus.running ?? 0}
          caption={current ? `${current.name} 기준` : `GPU ${s?.jobs.gpu_in_use ?? 0}개 점유`}
        />
        <Stat
          status="pending"
          label="대기"
          value={s?.jobs.by_status.queued ?? 0}
          caption={`큐 대기 · GPU ${s?.jobs.gpu_in_use ?? 0}개 점유${current ? ' · 전체 기준' : ''}`}
        />
        <Stat
          status="succeeded"
          label="성공률"
          value={rate === null ? '—' : `${rate}%`}
          caption={`끝난 실행 ${finished}건 기준`}
        />
        <Stat
          label="등록 모델"
          value={s?.models.active ?? 0}
          caption={`전체 ${s?.models.total ?? 0}종 중 활성`}
        />
      </div>

      {/*  Anything waiting on a person comes before anything merely informative */}
      {s && s.models.pending_approval.length > 0 && (
        <Panel
          title="승인 대기"
          description={`${s.models.pending_approval.length}종`}
          actions={
            <Button variant="outline" size="sm" asChild>
              <Link to="/models">
                처리하기
                <ArrowRight />
              </Link>
            </Button>
          }
        >
          <div className="flex flex-wrap gap-2">
            {s.models.pending_approval.map((m) => (
              <Badge key={`${m.model_id}:${m.version}`} variant="outline" className="gap-2">
                <StatusDot status="pending" />
                <span className="font-mono">
                  {m.model_id}:{m.version}
                </span>
                <span className="text-muted-foreground">{m.kind}</span>
              </Badge>
            ))}
          </div>
        </Panel>
      )}

      <Panel
        title="최근 실행"
        description={current ? `${current.name} ${s?.runs.total ?? 0}건` : `전체 ${s?.runs.total ?? 0}건`}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link to="/monitor">
              전체 보기
              <ArrowRight />
            </Link>
          </Button>
        }
        bodyClassName="p-0"
      >
        {s?.runs.recent.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[200px]">실행 식별자</TableHead>
                <TableHead className="w-[100px]">상태</TableHead>
                <TableHead>워크플로</TableHead>
                <TableHead className="w-[160px]">단계 진척</TableHead>
                <TableHead className="w-[130px]">개시 시각</TableHead>
                <TableHead className="w-[110px]">소요</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {s.runs.recent.map((r) => (
                <TableRow key={r.run_id}>
                  <TableCell className="font-mono text-[12.5px] whitespace-nowrap">
                    {r.run_id}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={r.status} />
                  </TableCell>
                  <TableCell>{r.workflow_id ?? '—'}</TableCell>
                  <TableCell>
                    {/*  Only the counts come back here, so the segments are rebuilt from them */}
                    <StageProgress
                      stages={Array.from({ length: r.stages_total }, (_, i) => ({
                        status: i < r.stages_done ? 'succeeded' : 'pending',
                      }))}
                    />
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                    {formatTime(r.started_at)}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                    {duration(r.started_at, r.finished_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty>실행 기록이 없습니다. 「실행 시작」으로 첫 실행을 띄우십시오.</Empty>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="워크플로"
          description={`${s?.workflows.total ?? 0}종`}
          actions={
            <Button variant="outline" size="sm" asChild>
              <Link to="/studio">
                스튜디오
                <ArrowRight />
              </Link>
            </Button>
          }
          bodyClassName={s?.workflows.items.length ? 'p-0' : undefined}
        >
          {s?.workflows.items.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead className="w-[80px]">판번호</TableHead>
                  <TableHead className="w-[80px]">노드</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {s.workflows.items.map((w) => (
                  <TableRow key={w.workflow_id}>
                    <TableCell>
                      <div>{w.name}</div>
                      <div className="text-muted-foreground font-mono text-[11.5px]">
                        {w.workflow_id}
                        {w.is_builtin && <span className="ml-2">기본 제공</span>}
                      </div>
                    </TableCell>
                    <TableCell className="tabular text-muted-foreground">v{w.version}</TableCell>
                    <TableCell className="tabular">{w.nodes}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty>등록된 워크플로가 없습니다.</Empty>
          )}
        </Panel>

        <Panel
          title="최근 활동"
          actions={
            <Button variant="outline" size="sm" asChild>
              <Link to="/operations">
                감사 기록
                <ArrowRight />
              </Link>
            </Button>
          }
        >
          {s?.audit.length ? (
            <div className="flex flex-col gap-2.5">
              {s.audit.map((a, i) => (
                <div key={i} className="flex items-baseline gap-3 text-[12.5px]">
                  <span className="text-muted-foreground tabular shrink-0 font-mono">
                    {formatTime(a.created_at)}
                  </span>
                  <span className="font-mono">{a.action}</span>
                  {a.target_id && (
                    <span
                      className="text-muted-foreground min-w-0 truncate font-mono"
                      title={a.target_id}
                    >
                      {a.target_id}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty>기록이 없습니다.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="파이프라인 단계" description="정형 실행이 거치는 순서">
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-3">
          {['msa', 'rfd3', 'bioemu', 'design', 'soluprot', 'af2', 'novelty'].map((id, i) => (
            <div key={id} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-muted-foreground/50 px-1">→</span>}
              <div
                className="flex flex-col rounded-md border px-2.5 py-1.5"
                title={stageTerm(id)?.hint}
              >
                <span className="font-mono text-[12.5px]">{id}</span>
                <span className="text-muted-foreground text-[11px]">{stageTerm(id)?.label}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </>
  )
}
