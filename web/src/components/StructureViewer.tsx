import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { api } from '../api/client'
import { cn } from '@/lib/utils'

/**
 * 3차원 구조 열람.
 *
 * 원본 콘솔이 쓰던 3Dmol.js 를 그대로 승계하되 CDN 이 아니라 번들로 넣는다 —
 * 폐쇄망에 설치하면 CDN 에 닿지 못해 뷰어가 통째로 뜨지 않는다(서체와 같은 이유).
 *
 * 표현은 무채색 콘솔에 맞춘다. 카툰 표현을 B-factor(pLDDT)로 칠해
 * **화면에서 색을 갖는 유일한 것이 「신뢰도」**가 되게 한다.
 */

type Mol = typeof import('3dmol')
type Viewer = ReturnType<Mol['createViewer']>

let loader: Promise<Mol> | null = null

/** 3Dmol 은 번들이 크다. 구조를 실제로 볼 때만 내려받게 갈라 둔다. */
function load3Dmol(): Promise<Mol> {
  loader ??= import('3dmol').then((m) => (m.default ?? m) as Mol)
  return loader
}

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
        //  B-factor 를 pLDDT 로 읽는다. AF2 산출물의 관행이고 원본도 같게 다뤘다
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
