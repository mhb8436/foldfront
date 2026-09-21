import { useState } from 'react'
import { ExternalLink, Plus, Search, X } from 'lucide-react'

import { api, type ReferenceHit } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { Panel, Empty, ErrorBox } from './Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'

//  The reference sources, with the label the panel shows for each.
const SOURCES: { value: string; label: string }[] = [
  { value: 'literature', label: '문헌' },
  { value: 'structure', label: '구조' },
  { value: 'protein', label: '단백질' },
  { value: 'family', label: '도메인' },
  { value: 'cluster', label: '군집' },
]

function sourceLabel(value: string): string {
  return SOURCES.find((s) => s.value === value)?.label ?? value
}

/**
 * Search external references and pin them to a run as evidence.
 *
 * Two lists: what a search turned up (not stored), and what is pinned to this
 * run (stored, and travels with it). Greyscale like the rest of the console -
 * the source is a small tag, the link the one affordance that leaves.
 */
export function EvidencePanel({ runId, canRun }: { runId: string; canRun: boolean }) {
  const [q, setQ] = useState('')
  const [source, setSource] = useState('literature')
  const [hits, setHits] = useState<ReferenceHit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pinned = useAsync(() => api.listEvidence(runId), [runId])

  async function search() {
    const query = q.trim()
    if (!query) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.searchReferences(query, source)
      setHits(res.items)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function attach(hit: ReferenceHit) {
    setError(null)
    try {
      await api.attachEvidence(runId, { source: hit.source, query: q.trim(), hit })
      pinned.reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function detach(evidenceId: string) {
    setError(null)
    try {
      await api.deleteEvidence(runId, evidenceId)
      pinned.reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const pinnedIds = new Set(pinned.data?.items.map((e) => e.hit.id))

  return (
    <Panel title="근거" description="설계 결정의 문헌 근거를 이 실행에 붙입니다.">
      <ErrorBox message={error ?? pinned.error} />

      {/*  Pinned first: it is the run's own record. Search is the way to add to it. */}
      {pinned.data?.items.length ? (
        <ul className="mb-4 flex flex-col gap-1.5">
          {pinned.data.items.map((e) => (
            <li key={e.evidence_id} className="flex items-start gap-2 text-[13px]">
              <span className="bg-muted mt-0.5 rounded px-1.5 py-0.5 text-[11px]">
                {sourceLabel(e.source)}
              </span>
              <span className="min-w-0 flex-1">
                {e.hit.url ? (
                  <a
                    href={e.hit.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 underline underline-offset-2"
                  >
                    {e.hit.title}
                    <ExternalLink className="size-3 shrink-0" />
                  </a>
                ) : (
                  e.hit.title
                )}
              </span>
              {canRun && (
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground mt-0.5"
                  onClick={() => detach(e.evidence_id)}
                  aria-label="근거 떼기"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Empty>아직 붙인 근거가 없습니다.</Empty>
      )}

      {canRun && (
        <>
          <div className="mt-3 flex gap-2">
            <div className="w-[92px] shrink-0">
              <Select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                aria-label="참조 소스"
              >
                {SOURCES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </Select>
            </div>
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') search()
              }}
              placeholder="검색어 (예: lysozyme)"
            />
            <Button variant="outline" size="sm" onClick={search} disabled={busy || !q.trim()}>
              <Search />
              검색
            </Button>
          </div>

          {hits && (
            <ul className="mt-3 flex flex-col gap-1.5">
              {hits.length === 0 && <Empty>검색 결과가 없습니다.</Empty>}
              {hits.map((h) => (
                <li key={h.id} className="flex items-start gap-2 text-[13px]">
                  <span className="min-w-0 flex-1">
                    {h.url ? (
                      <a
                        href={h.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 underline underline-offset-2"
                      >
                        {h.title}
                        <ExternalLink className="size-3 shrink-0" />
                      </a>
                    ) : (
                      h.title
                    )}
                    {typeof h.extra?.year === 'string' && (
                      <span className="text-muted-foreground ml-1.5 text-[11.5px]">{h.extra.year}</span>
                    )}
                  </span>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground mt-0.5 disabled:opacity-40"
                    onClick={() => attach(h)}
                    disabled={pinnedIds.has(h.id)}
                    aria-label="근거로 붙이기"
                    title={pinnedIds.has(h.id) ? '이미 붙인 근거' : '근거로 붙이기'}
                  >
                    <Plus className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  )
}
