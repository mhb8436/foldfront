import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, FolderOpen } from 'lucide-react'

import { useProject } from '@/lib/project'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * The project selector in the header.
 *
 * It sits in the header because what it changes is every screen at once. A
 * filter applied from inside a screen looks like it belongs to that screen,
 * and then the next screen appears to have lost data.
 *
 * 「전체」 is a choice on the same footing as any project, not a cleared
 * filter: runs made before projects existed belong to none, and they have to
 * remain reachable.
 */
export function ProjectPicker() {
  const { projects, current, select, loading } = useProject()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

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

  //  Nothing to choose between, so there is nothing to offer. The screens
  //  show everything, which is what they would show anyway.
  if (!loading && projects.length === 0) return null

  return (
    //  Always shown. The filter applies at every width, and a filter you
    //  cannot see reads as missing data.
    <div ref={ref} className="relative">
      <Button
        variant="outline"
        size="sm"
        className="font-normal"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-muted-foreground">프로젝트</span>
        <span className="max-w-[160px] truncate font-medium">{current?.name ?? '전체'}</span>
        <ChevronDown className="text-muted-foreground size-3.5" />
      </Button>

      {open && (
        <div
          role="menu"
          aria-label="프로젝트 선택"
          className="bg-popover absolute right-0 z-50 mt-1.5 max-h-[60vh] min-w-[248px] overflow-y-auto rounded-lg border p-1 shadow-md"
        >
          <Choice
            label="전체"
            hint="설치 전체"
            active={current === null}
            onSelect={() => {
              select(null)
              setOpen(false)
            }}
          />
          {projects.length > 0 && <div className="bg-border my-1 h-px" />}
          {projects.map((p) => (
            <Choice
              key={p.project_id}
              label={p.name}
              hint={p.project_id}
              active={current?.project_id === p.project_id}
              onSelect={() => {
                select(p.project_id)
                setOpen(false)
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function Choice({
  label,
  hint,
  active,
  onSelect,
}: {
  label: string
  hint: string
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      role="menuitemradio"
      aria-checked={active}
      type="button"
      onClick={onSelect}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left',
        'hover:bg-accent focus-visible:bg-accent outline-none',
      )}
    >
      <span className="flex size-4 shrink-0 items-center justify-center">
        {active ? <Check className="size-3.5" /> : <FolderOpen className="size-3.5 opacity-35" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px]">{label}</span>
        <span className="text-muted-foreground block truncate font-mono text-[11px]">{hint}</span>
      </span>
    </button>
  )
}
