import type { ReactNode } from 'react'

import type { RunStatus } from '../api/client'
import { cn } from '@/lib/utils'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const STATUS_LABEL: Record<string, string> = {
  pending: '대기',
  running: '실행 중',
  succeeded: '성공',
  failed: '실패',
  cancelled: '취소',
  queued: '대기',
  leased: '배정',
}

/*  화면에서 색을 갖는 것은 여기뿐이다. 나머지는 전부 무채색으로 둔다. */
const STATUS_DOT: Record<string, string> = {
  pending: 'bg-status-pending',
  queued: 'bg-status-pending',
  leased: 'bg-status-running',
  running: 'bg-status-running',
  succeeded: 'bg-status-succeeded',
  failed: 'bg-status-failed',
  cancelled: 'bg-status-cancelled',
}

export function StatusDot({ status, className }: { status: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('size-1.5 shrink-0 rounded-full', STATUS_DOT[status] ?? 'bg-muted-foreground', className)}
    />
  )
}

export function StatusBadge({ status }: { status: RunStatus | string }) {
  return (
    <Badge className={`badge ${status}`}>
      <StatusDot status={status} />
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

/**
 * 단계 진척을 칸으로 나눈다.
 * 백분율 막대를 쓰지 않는 이유 — 파이프라인 단계 수가 워크플로마다 정해져 있어
 * 「7칸 중 4칸」이 「57%」보다 실제 구조에 가깝고, 흑백 인쇄에서도 칸 수로 읽힌다.
 */
export function StageProgress({
  stages,
}: {
  stages: Array<{ status: string }>
}) {
  const done = stages.filter((s) => s.status === 'succeeded').length
  if (stages.length === 0) return <span className="text-muted-foreground">—</span>
  return (
    <div className="flex items-center gap-2">
      <div className="flex min-w-0 flex-1 gap-1">
        {stages.map((s, i) => (
          <span
            key={i}
            className={cn(
              'h-1 min-w-1.5 flex-1 rounded-full',
              s.status === 'succeeded'
                ? 'bg-foreground'
                : s.status === 'running'
                  ? 'bg-status-running'
                  : s.status === 'failed'
                    ? 'bg-status-failed'
                    : s.status === 'cancelled'
                      ? 'bg-status-cancelled'
                      : 'bg-border',
            )}
          />
        ))}
      </div>
      <span className="text-muted-foreground tabular shrink-0 font-mono text-[12px]">
        {done}/{stages.length}
      </span>
    </div>
  )
}

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <Card className={cn('panel', className)}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <span className="text-muted-foreground text-[12.5px]">{description}</span>}
        {actions && <CardAction>{actions}</CardAction>}
      </CardHeader>
      <CardContent className={bodyClassName}>{children}</CardContent>
    </Card>
  )
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null
  return <Alert className="error">{message}</Alert>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty text-muted-foreground py-8 text-center text-[13px]">{children}</p>
}

/** 완료 고지. 오류와 같은 자리에 같은 크기로 나와야 시선이 흔들리지 않는다. */
export function Notice({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="text-foreground rounded-lg border px-3.5 py-2.5 text-[13px]">{message}</div>
  )
}

/** 수치 요약 한 칸. */
export function Stat({
  label,
  value,
  caption,
  status,
}: {
  label: string
  value: ReactNode
  caption?: ReactNode
  status?: string
}) {
  return (
    <Card className="gap-2.5 p-5">
      <div className="text-muted-foreground flex items-center gap-2 text-[12.5px]">
        {status && <StatusDot status={status} />}
        {label}
      </div>
      <div className="tabular text-3xl leading-none font-semibold tracking-tight">{value}</div>
      {caption && <div className="text-muted-foreground text-[11.5px]">{caption}</div>}
    </Card>
  )
}

/** 바이트를 사람이 읽는 단위로. 산출물 목록에서 쓴다. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

/** 시각을 짧게. 목록에서 줄바꿈이 나지 않게 한다. */
export function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 걸린 시간. 시작만 있고 끝이 없으면 지금까지로 잰다. */
export function duration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const from = new Date(start).getTime()
  const to = end ? new Date(end).getTime() : Date.now()
  const sec = Math.max(0, Math.round((to - from) / 1000))
  if (sec < 60) return `${sec}초`
  if (sec < 3600) return `${Math.floor(sec / 60)}분 ${sec % 60}초`
  return `${Math.floor(sec / 3600)}시간 ${Math.floor((sec % 3600) / 60)}분`
}
