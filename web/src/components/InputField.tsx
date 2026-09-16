import { useId, useRef, useState, type DragEvent } from 'react'
import { Upload } from 'lucide-react'

import { api } from '../api/client'
import { formatBytes } from './Common'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import {
  formatResidues,
  looksLikePath,
  parseFasta,
  summarizePdb,
  type FastaSummary,
  type PdbSummary,
} from '@/lib/bio'

/**
 * One input to a run: a sequence or a structure.
 *
 * Three ways in, because a browser has three. Paste the content. Drop the
 * file, which uploads it and leaves the path. Or type a path, for someone
 * who already has the file on the server. The run reads all three the same
 * way, so the field does not need to know which it was given - it only
 * needs to say back what it understood, before anything is started on it.
 */

export type InputKind = 'fasta' | 'pdb'

const ACCEPT: Record<InputKind, string> = {
  fasta: '.fasta,.fa,.faa,.seq',
  pdb: '.pdb,.cif,.ent',
}

export function InputField({
  id,
  label,
  kind,
  value,
  onChange,
  onSummary,
  placeholder,
  disabled,
}: {
  id: string
  label: string
  kind: InputKind
  value: string
  onChange: (value: string) => void
  /** What the field made of the content, for the screen to act on. */
  onSummary?: (summary: FastaSummary | PdbSummary | null) => void
  placeholder?: string
  disabled?: boolean
}) {
  const [uploaded, setUploaded] = useState<{ name: string; size: number } | null>(null)
  const [summary, setSummary] = useState<FastaSummary | PdbSummary | null>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const hintId = useId()

  function summarize(text: string) {
    const s = kind === 'fasta' ? parseFasta(text) : summarizePdb(text)
    setSummary(s)
    onSummary?.(s)
  }

  function typed(next: string) {
    setUploaded(null)
    setError(null)
    onChange(next)
    if (!next.trim() || looksLikePath(next)) {
      setSummary(null)
      onSummary?.(null)
      return
    }
    summarize(next)
  }

  async function take(file: File) {
    setError(null)
    setBusy(true)
    try {
      //  Read it here for the summary, and send it up for the path. The two
      //  are independent: a file too large to upload still gets described,
      //  and a file that cannot be read locally still gets uploaded.
      try {
        summarize(await file.text())
      } catch {
        setSummary(null)
      }
      const stored = await api.uploadInput(file)
      setUploaded({ name: stored.name, size: stored.size_bytes })
      onChange(stored.path)
    } catch (e) {
      setError((e as Error).message)
      setSummary(null)
      onSummary?.(null)
    } finally {
      setBusy(false)
    }
  }

  function onDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    const file = e.dataTransfer.files?.[0]
    if (file) void take(file)
  }

  const isPath = !uploaded && looksLikePath(value)

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-[12px]"
          disabled={disabled || busy}
          onClick={() => picker.current?.click()}
        >
          <Upload className="size-3.5" />
          {busy ? '올리는 중' : '파일 선택'}
        </Button>
        <input
          ref={picker}
          type="file"
          accept={ACCEPT[kind]}
          className="hidden"
          aria-label={`${label} 파일`}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void take(file)
            e.target.value = ''
          }}
        />
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'relative rounded-md transition-colors',
          dragging && 'ring-ring/50 ring-[3px]',
        )}
      >
        <textarea
          id={id}
          value={value}
          disabled={disabled || busy}
          aria-describedby={hintId}
          onChange={(e) => typed(e.target.value)}
          placeholder={placeholder}
          rows={kind === 'fasta' ? 4 : 2}
          spellCheck={false}
          className={cn(
            'border-input placeholder:text-muted-foreground flex w-full min-w-0 rounded-md border bg-transparent px-3 py-2 font-mono text-[12.5px] leading-relaxed transition-colors outline-none',
            'focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]',
            'disabled:cursor-not-allowed disabled:opacity-50',
            'resize-y',
          )}
        />
        {dragging && (
          <div className="bg-background/80 pointer-events-none absolute inset-0 flex items-center justify-center rounded-md text-[13px] font-medium">
            놓으면 올립니다
          </div>
        )}
      </div>

      {/*  What the field understood. Absent when there is nothing to say -
          a line that always shows something is a line nobody reads. */}
      <p id={hintId} className="text-muted-foreground min-h-4 text-[11.5px]">
        {error ? (
          <span className="text-destructive">{error}</span>
        ) : uploaded ? (
          <>
            <span className="text-foreground">{uploaded.name}</span> · {formatBytes(uploaded.size)}{' '}
            · 서버에 올렸습니다{summary && <> · {describe(kind, summary)}</>}
          </>
        ) : summary ? (
          describe(kind, summary)
        ) : isPath ? (
          '서버에 있는 파일 경로로 읽습니다.'
        ) : (
          `${kind === 'fasta' ? 'FASTA' : 'PDB'} 내용을 붙여넣거나 파일을 끌어다 놓으십시오.`
        )}
      </p>
    </div>
  )
}

function describe(kind: InputKind, s: FastaSummary | PdbSummary): string {
  if (kind === 'fasta') {
    const f = s as FastaSummary
    if (!f.ok) return 'FASTA 로 읽히지 않습니다. 첫 줄이 > 로 시작해야 합니다.'
    return `서열 ${f.records.length}개 · ${formatResidues(f.records)}`
  }
  const p = s as PdbSummary
  if (!p.ok) return 'PDB 로 읽히지 않습니다. ATOM 기록이 없습니다.'
  return `체인 ${p.chains.join(', ') || '없음'} · 원자 ${p.atoms.toLocaleString()}개`
}
