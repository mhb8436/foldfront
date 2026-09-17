import { useEffect, useRef, useState } from 'react'
import { SendHorizontal } from 'lucide-react'

import { api } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { ErrorBox, Panel, StatusDot } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { useProject } from '@/lib/project'
import { cn } from '@/lib/utils'

/**
 * The design copilot.
 *
 * A local model that answers about this installation's own runs, from facts
 * the server hands it each turn. It starts nothing. What it was shown is
 * listed beside every answer, because an answer from a language model is
 * only as good as what it was allowed to see - and a wrong one should be
 * traceable to a missing fact rather than trusted.
 */

interface Message {
  role: 'user' | 'assistant'
  content: string
  /** For an answer: what the server put in front of the model. */
  context?: string[]
}

export function Copilot() {
  const status = useAsync(() => api.copilotStatus(), [])
  const { current } = useProject()
  const [runId, setRunId] = useState('')
  const [draft, setDraft] = useState('')
  //  Kept for the tab: the model's own answers send people to other screens,
  //  and coming back to an empty thread would make that advice cost the thread.
  const [messages, setMessages] = useState<Message[]>(() => {
    try {
      return JSON.parse(window.sessionStorage.getItem('foldfront.copilot') ?? '[]') as Message[]
    } catch {
      return []
    }
  })
  useEffect(() => {
    try {
      window.sessionStorage.setItem('foldfront.copilot', JSON.stringify(messages.slice(-40)))
    } catch {
      //  Storage refused; the thread lives for this mount only.
    }
  }, [messages])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    //  Optional call: jsdom has no scrollIntoView, and a test is not a place to scroll.
    bottom.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages.length, busy])

  async function send() {
    const content = draft.trim()
    if (!content || busy) return
    setError(null)
    setDraft('')
    const next: Message[] = [...messages, { role: 'user', content }]
    setMessages(next)
    setBusy(true)
    try {
      const run = runId.trim()
      if (run && !/^run-[0-9a-f]{6,}$/i.test(run)) {
        throw new Error(`실행 식별자 형식이 아닙니다: ${run}. run- 으로 시작하는 식별자를 적으십시오.`)
      }
      const out = await api.copilotChat({
        messages: next.map(({ role, content }) => ({ role, content })),
        project_id: current?.project_id,
        run_id: run || undefined,
      })
      setMessages([...next, { role: 'assistant', content: out.reply, context: out.context_used }])
    } catch (e) {
      setError((e as Error).message)
      //  The question stays in the box so it can be sent again.
      setDraft(content)
      setMessages(messages)
    } finally {
      setBusy(false)
    }
  }

  const available = status.data?.available ?? false

  return (
    <>
      <PageHeader
        title="설계 Copilot"
        description="이 설치의 실행·회차·워크플로를 근거로 답합니다. 실행은 시작하지 않습니다 — 그건 실행 준비 화면에서 하십시오."
        actions={
          messages.length > 0 && (
            <Button variant="outline" size="sm" onClick={() => setMessages([])}>
              대화 비우기
            </Button>
          )
        }
      />
      <ErrorBox message={error ?? status.error} />

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <Panel
          title="대화"
          description={
            status.loading ? (
              '모델 확인 중'
            ) : (
              <span className="inline-flex items-center gap-1.5">
                <StatusDot status={available ? 'succeeded' : 'failed'} />
                {available
                  ? `로컬 모델 ${status.data!.model}`
                  : status.error
                    ? `상태를 읽지 못했습니다 — ${status.error}`
                    : `모델에 닿지 못했습니다 (${status.data?.url ?? ''})`}
              </span>
            )
          }
          bodyClassName="flex h-[560px] flex-col p-0"
        >
          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {messages.length === 0 && (
              <p className="text-muted-foreground text-[13px]">
                예 — 「이 프로젝트의 최근 실행을 요약해 줘」 · 「run-… 의 용해도 통과율은?」 ·
                「다음 회차에는 무엇을 바꿔야 할까?」
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div
                  className={cn(
                    'max-w-[85%] rounded-lg px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap',
                    m.role === 'user' ? 'bg-foreground text-background' : 'bg-muted',
                  )}
                >
                  {m.content}
                  {m.context && m.context.length > 0 && (
                    <div className="text-muted-foreground mt-2 border-t pt-2 text-[11.5px]">
                      참고: {m.context.join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="text-muted-foreground text-[12.5px]" role="status">
                답을 쓰는 중…
              </div>
            )}
            <div ref={bottom} />
          </div>

          <form
            className="flex gap-2 border-t p-3"
            onSubmit={(e) => {
              e.preventDefault()
              void send()
            }}
          >
            <Input
              aria-label="질문"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={available ? '무엇이든 물어보십시오' : '모델이 연결되면 물을 수 있습니다'}
              disabled={!available || busy}
            />
            <Button type="submit" size="sm" disabled={!available || busy || !draft.trim()}>
              <SendHorizontal />
              보내기
            </Button>
          </form>
        </Panel>

        <Panel title="범위" description="답의 근거가 되는 것">
          <div className="flex flex-col gap-3.5 text-[12.5px]">
            <div>
              <div className="text-muted-foreground text-[11.5px]">프로젝트</div>
              <div className="font-medium">{current ? current.name : '전체'}</div>
              <div className="text-muted-foreground text-[11.5px]">머리말의 선택기로 바꿉니다.</div>
            </div>
            <Field>
              <Label htmlFor="copilot-run">실행 식별자 (선택)</Label>
              <Input
                id="copilot-run"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
                placeholder="run-…"
                className="font-mono text-[12.5px]"
              />
              <p className="text-muted-foreground text-[11.5px]">
                적으면 그 실행의 단계·지표·사건까지 근거에 들어갑니다.
              </p>
            </Field>
            <p className="text-muted-foreground text-[11.5px]">
              모델은 이 기계에서 돕니다. 서열·결과가 밖으로 나가지 않습니다.
            </p>
            <p className="text-muted-foreground border-t pt-3 text-[11.5px]">
              {/*  Said here, not buried in docs: the person deciding whether to
                  act on an answer needs to know what kind of thing it is. */}
              언어 모델의 답입니다. 「참고」에 적힌 현황 안에서 답하도록 묶어 두었지만, 수치와 상태는 실행 감시·결과
              분석 화면에서 확인하고 행동하십시오.
            </p>
          </div>
        </Panel>
      </div>
    </>
  )
}
