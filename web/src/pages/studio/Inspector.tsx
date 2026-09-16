import { useEffect } from 'react'
import { Copy, Trash2, X } from 'lucide-react'

import type { NodeKind } from '../../api/client'
import { stageTerm } from '@/lib/glossary'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import type { IssueField } from './checks'

/**
 * The node editor, floating over the canvas.
 *
 * It sits inside the canvas box rather than beside it. A column beside the
 * canvas would take its width from the graph, and a graph that shrinks to fit
 * is a graph whose labels stop being readable - which costs exactly what the
 * canvas is for. Overlapping costs the right-hand 300px of *view*, and the
 * view can be dragged out from under the panel.
 *
 * It stays open while you move between nodes: clicking another node swaps the
 * contents rather than closing. Editing a branch usually means looking at the
 * node before it, and a panel that closed each time would make that a loop.
 */

const KIND_LABEL: Record<NodeKind, string> = {
  model: '모델',
  transform: '변환',
  branch: '조건 분기',
  fanout: '병렬 분기',
  join: '합류',
}

/** The legend swatch for a kind, so the header says which shape this is. */
export function KindMark({ kind, className }: { kind: NodeKind; className?: string }) {
  const shape: Record<NodeKind, string> = {
    model: 'bg-foreground',
    transform: 'bg-muted border',
    branch: 'border-foreground rounded-full border-2',
    fanout: 'border-foreground border border-dashed',
    join: 'border-foreground border',
  }
  return (
    <span
      aria-hidden
      className={cn('inline-block h-3.5 w-7 rounded-[3px]', shape[kind], className)}
    />
  )
}

/** The element 「고치기」 lands on, per kind of problem. */
const FIELD_ID: Record<IssueField, string> = {
  model: 'ins-model',
  condition: 'ins-condition',
  edges: 'ins-edges',
  links: 'ins-edges',
}

export interface InspectorEdge {
  id: string
  source: string
  target: string
  branch: 'true' | 'false' | null
}

export function Inspector({
  nodeId,
  kind,
  modelIds,
  model,
  onModel,
  condition,
  onCondition,
  outgoing,
  incoming,
  onEdgeBranch,
  onDuplicate,
  onDelete,
  onClose,
  focus,
  readOnly,
}: {
  nodeId: string
  kind: NodeKind
  modelIds: string[]
  model: string
  onModel: (modelId: string) => void
  condition: string
  onCondition: (condition: string) => void
  outgoing: InspectorEdge[]
  incoming: InspectorEdge[]
  onEdgeBranch: (edgeId: string, branch: 'true' | 'false' | null) => void
  onDuplicate: () => void
  onDelete: () => void
  onClose: () => void
  /** Which field the panel was opened for, from 「고치기」 or a menu. */
  focus?: IssueField
  readOnly: boolean
}) {
  //  Opening the panel to fix one thing should land on that thing. Without
  //  this, 「고치기」 leaves you to find the field the message named.
  //
  //  By id rather than by ref: these are plain function components, so a ref
  //  would mean wrapping the shared primitives in forwardRef for one caller.
  useEffect(() => {
    if (!focus) return
    const field = document.getElementById(FIELD_ID[focus])
    if (focus === 'edges' || focus === 'links') {
      field?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      return
    }
    ;(field as HTMLElement | null)?.focus()
  }, [focus, nodeId])

  return (
    <aside
      aria-label={`${nodeId} 편집`}
      //  Stops a drag inside the panel from panning the canvas underneath it.
      onPointerDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.stopPropagation()}
      className={cn(
        'bg-card absolute top-3 right-3 bottom-3 z-20 flex w-[300px] flex-col',
        'rounded-lg border shadow-lg',
      )}
    >
      <header className="flex items-start gap-2.5 border-b px-3.5 py-3">
        <KindMark kind={kind} className="mt-1 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[13px] font-semibold" title={nodeId}>
            {nodeId}
          </div>
          <div className="text-muted-foreground text-[11.5px]">
            {KIND_LABEL[kind]} · 들어오는 {incoming.length} · 나가는 {outgoing.length}
          </div>
        </div>
        <Button variant="ghost" size="icon" aria-label="닫기" onClick={onClose}>
          <X />
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-3.5 py-3.5">
        <div className="flex flex-col gap-4">
          {kind === 'model' && (
            <Field>
              <Label htmlFor="ins-model">실행할 모델</Label>
              <Select
                id="ins-model"
                value={model}
                disabled={readOnly}
                onChange={(e) => onModel(e.target.value)}
              >
                <option value="">— 미지정 —</option>
                {modelIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </Select>
              <p className="text-muted-foreground text-[11.5px]">
                {stageTerm(model)?.hint ?? '등록된 활성 모델만 나옵니다.'}
              </p>
            </Field>
          )}

          {kind === 'branch' && (
            <Field>
              <Label htmlFor="ins-condition">조건식</Label>
              <Input
                id="ins-condition"
                value={condition}
                disabled={readOnly}
                onChange={(e) => onCondition(e.target.value)}
                placeholder="soluprot.pass_rate > 0.3"
                className="font-mono text-[13px]"
              />
              <p className="text-muted-foreground text-[11.5px]">
                <code className="font-mono">노드.지표 &gt; 값</code> 형태로 씁니다. 앞선 단계가
                낸 지표를 읽습니다.
              </p>
            </Field>
          )}

          {kind === 'transform' && (
            <p className="text-muted-foreground text-[12.5px]">
              앞 단계의 산출물을 다음 단계의 입력 형태로 맞춥니다. 별도 설정이 없습니다.
            </p>
          )}

          {(kind === 'fanout' || kind === 'join') && (
            <p className="text-muted-foreground text-[12.5px]">
              {kind === 'fanout'
                ? '나가는 간선마다 같은 입력으로 동시에 실행합니다.'
                : '들어오는 간선 중 하나라도 끝나면 진행합니다.'}
            </p>
          )}

          {outgoing.length > 0 && (
            <div id="ins-edges" className="flex flex-col gap-2">
              <h4 className="text-[12.5px] font-semibold">나가는 간선</h4>
              {outgoing.map((e) => (
                <div key={e.id} className="flex items-center gap-2">
                  <span className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-[12px]">
                    → {e.target}
                  </span>
                  {kind === 'branch' ? (
                    <div className="w-[104px] shrink-0">
                      <Select
                        aria-label={`${e.target} 로 가는 간선의 가지`}
                        value={e.branch ?? ''}
                        disabled={readOnly}
                        onChange={(ev) =>
                          onEdgeBranch(e.id, (ev.target.value || null) as 'true' | 'false' | null)
                        }
                      >
                        <option value="">— 미지정 —</option>
                        <option value="true">참</option>
                        <option value="false">거짓</option>
                      </Select>
                    </div>
                  ) : (
                    <span className="text-muted-foreground text-[11.5px]">순차</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {incoming.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <h4 className="text-[12.5px] font-semibold">들어오는 간선</h4>
              {incoming.map((e) => (
                <span key={e.id} className="text-muted-foreground truncate font-mono text-[12px]">
                  {e.source} →
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <footer className="flex gap-2 border-t px-3.5 py-3">
        <Button variant="outline" size="sm" disabled={readOnly} onClick={onDuplicate}>
          <Copy />
          복제
        </Button>
        <Button variant="outline" size="sm" disabled={readOnly} onClick={onDelete}>
          <Trash2 />
          삭제
        </Button>
      </footer>
    </aside>
  )
}
