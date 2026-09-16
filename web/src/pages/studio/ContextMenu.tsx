import { useEffect, useRef, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * The right-click menu on the workflow canvas.
 *
 * What it is for is reach: adding a node used to mean going up to the toolbar
 * and then dragging what appeared back to where it was wanted, and marking an
 * edge true or false meant finding it again in a table below the canvas. Both
 * belong where the pointer already is.
 *
 * It closes on a click anywhere, on Escape, and on scroll, because a menu left
 * floating over a canvas that has moved underneath it is worse than no menu.
 */

export interface MenuItem {
  label: string
  hint?: string
  icon?: ReactNode
  onSelect: () => void
  danger?: boolean
  /** Marks the current value, for items that pick one of a set. */
  active?: boolean
}

export interface MenuState {
  kind: 'pane' | 'node' | 'edge' | 'model'
  /** Viewport coordinates of the click. */
  x: number
  y: number
  /** Node or edge id, absent for the pane. */
  id?: string
}

export function ContextMenu({
  state,
  title,
  items,
  onClose,
}: {
  state: MenuState
  title: string
  items: MenuItem[]
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    //  Capture, so a click that also does something else still closes this.
    //  Capture runs before the menu's own handler, so stopPropagation there
    //  would come too late - the target is checked here instead.
    const close = (e: Event) => {
      if (e.target instanceof Node && ref.current?.contains(e.target)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('pointerdown', close, true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('pointerdown', close, true)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
    }
  }, [onClose])

  //  Keep the menu on screen when the click lands near an edge of the window.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const box = el.getBoundingClientRect()
    if (box.right > window.innerWidth) el.style.left = `${window.innerWidth - box.width - 8}px`
    if (box.bottom > window.innerHeight) el.style.top = `${window.innerHeight - box.height - 8}px`
  }, [state])

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={title}
      className="bg-popover fixed z-50 min-w-[196px] overflow-hidden rounded-lg border shadow-md"
      style={{ left: state.x, top: state.y }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="text-muted-foreground border-b px-3 py-2 text-[11.5px] font-medium">
        {title}
      </div>
      <div className="p-1">
        {items.map((item) => (
          <button
            key={item.label}
            role="menuitem"
            type="button"
            onClick={() => {
              item.onSelect()
              onClose()
            }}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[13px]',
              'hover:bg-accent focus-visible:bg-accent outline-none',
              item.danger && 'text-destructive',
            )}
          >
            {item.icon && <span className="flex size-4 items-center justify-center">{item.icon}</span>}
            <span className="flex-1">{item.label}</span>
            {item.active && <span className="text-muted-foreground text-[11px]">현재</span>}
            {item.hint && !item.active && (
              <span className="text-muted-foreground font-mono text-[11px]">{item.hint}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
