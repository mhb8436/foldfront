import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { api } from '../api/client'
import { cn } from '@/lib/utils'
import { load3Dmol, type Viewer } from '@/lib/mol'

/**
 * Three-dimensional structure viewing.
 *
 * 3Dmol.js, as the original console used, but bundled rather than pulled from
 * a CDN (see lib/mol).
 *
 * The cartoon is coloured by B-factor, read as pLDDT, which makes confidence
 * the one thing a structure says in colour on an otherwise greyscale screen.
 */

export function StructureViewer({
  runId,
  path,
  label,
  className,
  height = 320,
}: {
  runId: string
  path: string
  label?: string
  className?: string
  height?: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    let viewer: Viewer | null = null
    setLoading(true)
    setError(null)

    Promise.all([load3Dmol(), api.artifactText(runId, path)])
      .then(([mol, pdb]) => {
        if (!alive || !host.current) return
        host.current.innerHTML = ''
        viewer = mol.createViewer(host.current, { backgroundColor: 'white' })
        viewer.addModel(pdb, 'pdb')
        //  B-factor holds pLDDT by convention in AF2 output, which is how
        //  the original read it too
        viewer.setStyle({}, { cartoon: { colorscheme: { prop: 'b', gradient: 'roygb', min: 50, max: 90 } } })
        viewer.zoomTo()
        viewer.render()
        setLoading(false)
      })
      .catch((e) => {
        if (!alive) return
        setError((e as Error).message)
        setLoading(false)
      })

    return () => {
      alive = false
      viewer?.clear()
    }
  }, [runId, path])

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <div className="text-muted-foreground truncate font-mono text-[12px]" title={path}>
          {label}
        </div>
      )}
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
      {!loading && !error && (
        <div className="text-muted-foreground flex items-center gap-2 text-[11px]">
          <span>신뢰도(pLDDT)</span>
          <span
            className="h-1.5 w-24 rounded-full"
            style={{ background: 'linear-gradient(90deg,#c8352b,#d79b3c,#1e6e68,#1b6fb8)' }}
          />
          <span>50 → 90</span>
        </div>
      )}
    </div>
  )
}
