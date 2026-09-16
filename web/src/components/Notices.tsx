import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell } from 'lucide-react'

import { api, type Notice } from '../api/client'
import { usePolling } from '../hooks/useAsync'
import { Button } from '@/components/ui/button'
import { useIdentity } from '@/lib/identity'
import { StatusDot, formatTime } from './Common'
import { cn } from '@/lib/utils'

/**
 * The bell.
 *
 * It rings for things somebody has to do something about, and for nothing
 * else. The audit trail already records what happened; a bell that also
 * reported every completed run would be a second activity feed, and the
 * number on it would never reach zero - at which point people stop reading it
 * and it may as well not be there.
 *
 * Which notices exist is decided by the server, because it depends on who is
 * asking: an operator is told about approvals and about the installation
 * running without authentication, and a reader is not, since neither is
 * theirs to act on.
 *
 * What has been read is remembered per browser rather than on the server. It
 * is one person's place in a list, not a fact about the system, and a browser
 * that cannot store it simply shows everything as new.
 */

const READ = 'foldfront.notices.read'

function readIds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(READ)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function rememberRead(ids: Set<string>) {
  try {
    //  Only what is still live, so the list cannot grow without bound as runs
    //  come and go.
    window.localStorage.setItem(READ, JSON.stringify([...ids]))
  } catch {
    //  Storage refused. Everything shows as new, which is the harmless way
    //  for this to fail.
  }
}

const SEVERITY_DOT: Record<Notice['severity'], string> = {
  action: 'pending',
  warning: 'failed',
}

export function Notices() {
  //  Slower than the run screens: these are things that will still be true in
  //  half a minute, and the bell is not what anyone is watching.
  const notices = usePolling(() => api.notices(), 30000, [])
  const [open, setOpen] = useState(false)
  const [read, setRead] = useState<Set<string>>(() => readIds())
  const ref = useRef<HTMLDivElement>(null)
  const { canRun } = useIdentity()

  const items = notices.data?.items ?? []
  const unread = items.filter((n) => !read.has(n.id))

  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e.target instanceof Node && ref.current?.contains(e.target)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', close, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  //  Opening the list is reading it. Marking each one separately would mean
  //  clearing a badge by hand, which is work the bell exists to save.
  function markRead() {
    //  Not before the first answer: marking an empty list read would wipe
    //  what was remembered, and everything would ring again when it arrived.
    if (!notices.data) return
    const next = new Set(items.map((n) => n.id))
    setRead(next)
    rememberRead(next)
  }

  const navigate = useNavigate()
  const [repairing, setRepairing] = useState<string | null>(null)
  const [repairError, setRepairError] = useState<string | null>(null)

  async function repair(runId: string) {
    setRepairing(runId)
    setRepairError(null)
    try {
      await api.reconcileRun(runId)
      //  Straight away rather than on the next poll: pressing a button and
      //  watching nothing change reads as the button not working.
      notices.reload()
    } catch (e) {
      //  Said, not swallowed. A button that fails silently is one that
      //  appears not to work, which is the thing this row exists to avoid.
      setRepairError((e as Error).message)
    } finally {
      setRepairing(null)
    }
  }

  return (
    <div ref={ref} className="relative">
      <Button
        variant="ghost"
        size="icon"
        className="relative size-8"
        aria-label={unread.length ? `알림 ${unread.length}건` : '알림'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          if (!open) markRead()
          setOpen((v) => !v)
        }}
      >
        <Bell className="size-4" />
        {unread.length > 0 && (
          <span
            //  A count rather than a plain dot: whether it is one thing or
            //  nine changes whether you deal with it now.
            className="bg-status-failed text-background absolute -top-0.5 -right-0.5 flex min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-4 font-semibold tabular-nums"
          >
            {unread.length > 9 ? '9+' : unread.length}
          </span>
        )}
      </Button>

      {open && (
        <div
          role="menu"
          aria-label="알림"
          className="bg-popover absolute right-0 z-50 mt-1.5 max-h-[60vh] w-[340px] overflow-y-auto rounded-lg border shadow-md"
        >
          <div className="text-muted-foreground border-b px-3.5 py-2.5 text-[11.5px] font-medium">
            {items.length ? `처리를 기다리는 ${items.length}건` : '알림'}
          </div>

          {items.length === 0 ? (
            <p className="text-muted-foreground px-3.5 py-6 text-center text-[12.5px]">
              손을 기다리는 일이 없습니다.
            </p>
          ) : (
            <div className="p-1">
              {items.map((n) => (
                <div key={n.id} className="flex items-start gap-1">
                  <button
                    {...(n.href ? { role: 'menuitem' as const } : {})}
                    type="button"
                    disabled={!n.href}
                    onClick={() => {
                      if (!n.href) return
                      navigate(n.href)
                      setOpen(false)
                    }}
                    className={cn(
                      'flex min-w-0 flex-1 items-start gap-2.5 rounded-md px-2.5 py-2 text-left',
                      n.href && 'hover:bg-accent focus-visible:bg-accent cursor-pointer outline-none',
                    )}
                  >
                    <StatusDot status={SEVERITY_DOT[n.severity]} className="mt-1.5" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium">{n.title}</span>
                      <span className="text-muted-foreground block text-[11.5px] break-words">
                        {n.detail}
                      </span>
                      {n.at && (
                        <span className="text-muted-foreground tabular block text-[11px]">
                          {formatTime(n.at)}
                        </span>
                      )}
                    </span>
                  </button>

                  {/*  Only where there is a repair to run. Saying a run has
                      stopped and leaving nothing to press is half a message. */}
                  {/*  Only for someone who may repair it: the request would
                      answer 403, and a button that does nothing is worse
                      than no button. */}
                  {n.kind === 'run.stalled' && n.target_id && canRun && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-1.5 shrink-0"
                      disabled={repairing === n.target_id}
                      onClick={() => repair(n.target_id!)}
                    >
                      {repairing === n.target_id ? '점검 중' : '되살리기'}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
          {repairError && (
            <p className="text-destructive border-t px-3.5 py-2 text-[11.5px]">{repairError}</p>
          )}
        </div>
      )}
    </div>
  )
}
