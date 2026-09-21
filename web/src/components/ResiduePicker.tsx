import { useEffect, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'

import { load3Dmol, type Viewer } from '@/lib/mol'

/**
 * Pick residues to hold fixed in design.
 *
 * The researcher clicks residues in the 3D structure; each toggles into the
 * selection, keyed by chain. The result is {chain: [resi, ...]} - the shape
 * the design endpoint takes (clients/proteinmpnn.py fixed_positions), and
 * what Setup puts on the run request. resi is the PDB residue number, which
 * is what the original passes straight through.
 *
 * The 3D canvas is the one place colour is allowed on an otherwise greyscale
 * console: the cartoon is coloured by chain so the structure reads, and a
 * held residue turns to sticks so it stands out. The list below is greyscale.
 */

export type FixedPositions = Record<string, number[]>

/** The fields 3Dmol hands a click callback that we read. */
interface ClickedAtom {
  chain: string
  resi: number
  resn?: string
}

export function count(value: FixedPositions): number {
  return Object.values(value).reduce((n, r) => n + r.length, 0)
}

/** Add or remove one residue, keeping each chain's list sorted and unique. */
export function toggle(value: FixedPositions, chain: string, resi: number): FixedPositions {
  const next: FixedPositions = {}
  for (const [c, list] of Object.entries(value)) next[c] = [...list]
  const list = next[chain] ?? []
  next[chain] = list.includes(resi)
    ? list.filter((r) => r !== resi)
    : [...list, resi].sort((a, b) => a - b)
  if (!next[chain].length) delete next[chain]
  return next
}

/** Cartoon by chain, held residues as sticks. Re-applied on every change. */
function paint(viewer: Viewer, value: FixedPositions): void {
  viewer.setStyle({}, { cartoon: { colorscheme: 'chainHetatm' } })
  for (const [chain, resis] of Object.entries(value)) {
    if (resis.length) viewer.addStyle({ chain, resi: resis }, { stick: { radius: 0.3 } })
  }
  viewer.render()
}

export function ResiduePicker({
  pdb,
  value,
  onChange,
  disabled,
  height = 320,
}: {
  pdb: string
  value: FixedPositions
  onChange: (value: FixedPositions) => void
  disabled?: boolean
  height?: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  //  The click handler is bound to the model once, but must always toggle
  //  against the latest selection - hence a ref rather than a closed-over value.
  const valueRef = useRef(value)
  valueRef.current = value
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const disabledRef = useRef(disabled)
  disabledRef.current = disabled

  //  Build the viewer when the structure changes. Clicking is wired here once.
  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    viewerRef.current = null

    load3Dmol()
      .then((mol) => {
        if (!alive || !host.current) return
        host.current.innerHTML = ''
        const viewer = mol.createViewer(host.current, { backgroundColor: 'white' })
        viewer.addModel(pdb, 'pdb')
        viewer.setClickable({}, true, (atom: ClickedAtom) => {
          if (disabledRef.current || !atom || atom.resi == null) return
          const next = toggle(valueRef.current, atom.chain || '_', atom.resi)
          onChangeRef.current(next)
        })
        viewer.zoomTo()
        paint(viewer, valueRef.current)
        viewerRef.current = viewer
        setLoading(false)
      })
      .catch((e) => {
        if (!alive) return
        setError((e as Error).message)
        setLoading(false)
      })

    return () => {
      alive = false
      viewerRef.current?.clear()
      viewerRef.current = null
    }
  }, [pdb])

  //  Repaint whenever the selection changes, from a click or a removed chip.
  useEffect(() => {
    if (viewerRef.current) paint(viewerRef.current, value)
  }, [value])

  const chips = Object.entries(value).flatMap(([chain, resis]) =>
    resis.map((resi) => ({ chain, resi })),
  )
  const total = count(value)

  return (
    <div className="flex flex-col gap-2">
      <div
        className="relative overflow-hidden rounded-md border bg-white"
        style={{ height }}
      >
        <div ref={host} className="absolute inset-0" />
        {loading && (
          <div className="text-muted-foreground absolute inset-0 flex items-center justify-center gap-2 text-[12.5px]">
            <Loader2 className="size-4 animate-spin" />
            구조를 불러오는 중입니다
          </div>
        )}
        {error && (
          <div className="text-destructive absolute inset-0 flex items-center justify-center px-4 text-center text-[12.5px]">
            {error}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between">
        <p className="text-muted-foreground text-[11.5px]">
          잔기를 누르면 고정 위치로 잡힙니다. 고정한 잔기는 설계에서 원래 서열을 유지합니다.
        </p>
        {total > 0 && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground text-[11.5px] underline"
            onClick={() => onChange({})}
            disabled={disabled}
          >
            {total}개 전체 해제
          </button>
        )}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map(({ chain, resi }) => (
            <span
              key={`${chain}:${resi}`}
              className="bg-muted inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11.5px]"
            >
              {chain}:{resi}
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => onChange(toggle(value, chain, resi))}
                disabled={disabled}
                aria-label={`${chain}:${resi} 고정 해제`}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
