import type { ReactNode } from 'react'

import type { RunStatus } from '../api/client'
import { cn } from '@/lib/utils'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const STATUS_LABEL: Record<string, string> = {
  pending: '대기',
  running: '실행 중',
  paused: '멈춤',
  succeeded: '성공',
  failed: '실패',
  cancelled: '취소',
  queued: '대기',
  leased: '배정',
}

/*  The only colour on any screen. Everything else is greyscale. */
const STATUS_DOT: Record<string, string> = {
  pending: 'bg-status-pending',
  queued: 'bg-status-pending',
  leased: 'bg-status-running',
  running: 'bg-status-running',
  succeeded: 'bg-status-succeeded',
  failed: 'bg-status-failed',
  cancelled: 'bg-status-cancelled',
}

/*  A held run needs to stand out, and a sixth colour is not available: the
    palette carries five statuses and nothing else on the screen has any. So
    it is told apart by shape - a ring rather than a disc - which is also what
    survives the black-and-white printing these screens are read on. */
const STATUS_RING: Record<string, string> = {
  paused: 'border-status-running',
}

export function StatusDot({ status, className }: { status: string; className?: string }) {
  const ring = STATUS_RING[status]
  return (
    <span
      aria-hidden
      className={cn(
        'size-1.5 shrink-0 rounded-full',
        ring ? cn('border-2 bg-transparent', ring) : STATUS_DOT[status] ?? 'bg-muted-foreground',
        className,
      )}
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
 * Stage progress as segments rather than a percentage bar.
 *
 * A workflow has a known number of stages, so "four of seven" describes what
 * happened and "57%" only approximates it. Segments also survive being printed
 * in black and white, where a filled bar does not.
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

/** A success notice. Same place and size as an error, so nothing jumps. */
export function Notice({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="text-foreground rounded-lg border px-3.5 py-2.5 text-[13px]">{message}</div>
  )
}

/** One number worth reading at a glance. */
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

/** Bytes in units a person reads. Used in artifact listings. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

/** A short timestamp, short enough not to wrap inside a table cell. */
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

/** Elapsed time. With a start but no end, measured to now. */
export function duration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const from = new Date(start).getTime()
  const to = end ? new Date(end).getTime() : Date.now()
  const sec = Math.max(0, Math.round((to - from) / 1000))
  if (sec < 60) return `${sec}초`
  if (sec < 3600) return `${Math.floor(sec / 60)}분 ${sec % 60}초`
  return `${Math.floor(sec / 3600)}시간 ${Math.floor((sec % 3600) / 60)}분`
}
