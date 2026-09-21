import { useState } from 'react'
import { Wand2 } from 'lucide-react'

import { api, type Plan } from '../../api/client'
import { ErrorBox, Notice, Panel } from '../../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'

/**
 * Draft a workflow from a sentence.
 *
 * The routing is the original's - `router.plan_from_prompt` reads the prompt
 * and says which stages it implies, what parameters it could infer and what
 * it still needs. This screen shows that answer and hands the draft to the
 * studio.
 *
 * It does not start anything, and it says so. A plan with required inputs
 * missing is shown as a plan with those inputs missing; the alternative,
 * filling them with defaults and running, spends GPU time on a guess.
 *
 * The quick prompts are here because the router matches on model names -
 * rfd3, bioemu, diffdock - and someone meeting the box for the first time
 * has no way to know that. They are examples of what it can read, not
 * suggestions about what to design.
 */

const QUICK: Array<{ label: string; prompt: string }> = [
  {
    label: '정형 체인 전체',
    prompt: '정렬부터 구조 예측까지 전 단계를 돌려 주십시오.',
  },
  {
    label: 'RFD3 백본 설계',
    prompt: 'rfd3 로 백본을 만들고 설계한 서열을 용해도로 거른 뒤 af2 까지 돌려 주십시오.',
  },
  {
    label: '용해도까지만',
    prompt: '설계하고 stop_after=soluprot 까지만 돌려 주십시오.',
  },
  {
    label: '리간드 결합 예측',
    prompt: 'diffdock 으로 리간드 결합을 예측해 주십시오.',
  },
]

export function Planner({ onOpen }: { onOpen?: (plan: Plan) => void }) {
  const [prompt, setPrompt] = useState('')
  const [targetFasta, setTargetFasta] = useState('')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function draft(text: string) {
    const asked = text.trim()
    if (!asked) return
    setBusy(true)
    setError(null)
    try {
      setPlan(
        await api.copilotPlan({
          prompt: asked,
          target_fasta: targetFasta.trim() || undefined,
        }),
      )
    } catch (e) {
      setError((e as Error).message)
      setPlan(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="계획 생성"
      description="문장으로 워크플로 초안을 만듭니다. 실행하지는 않습니다."
    >
      <div className="flex flex-col gap-3">
        <Field>
          <Label htmlFor="plan-prompt">무엇을 하시겠습니까</Label>
          <div className="flex gap-2">
            <Input
              id="plan-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !busy) draft(prompt)
              }}
              placeholder="rfd3 로 백본을 만들고 용해도로 거른 뒤 af2 까지 돌려 주십시오"
              className="text-[13px]"
            />
            <Button disabled={busy || !prompt.trim()} onClick={() => draft(prompt)}>
              <Wand2 />
              초안 만들기
            </Button>
          </div>
        </Field>

        <Field>
          <Label htmlFor="plan-fasta">대상 서열 (선택)</Label>
          <Input
            id="plan-fasta"
            value={targetFasta}
            onChange={(e) => setTargetFasta(e.target.value)}
            placeholder="MKTAYIAKQRQISFVKSHFSRQ…"
            className="font-mono text-[12.5px]"
          />
        </Field>

        {/*  The router reads model names. Without examples nobody would
             guess that, and every prompt would fall back to the chain. */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground text-[12px]">예시</span>
          {QUICK.map((q) => (
            <Button
              key={q.label}
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => {
                setPrompt(q.prompt)
                draft(q.prompt)
              }}
            >
              {q.label}
            </Button>
          ))}
        </div>

        <ErrorBox message={error} />
        {plan && <Drafted plan={plan} onOpen={onOpen} />}
      </div>
    </Panel>
  )
}

function Drafted({ plan, onOpen }: { plan: Plan; onOpen?: (plan: Plan) => void }) {
  const required = plan.questions.filter((q) => q.required)
  const optional = plan.questions.filter((q) => !q.required)

  return (
    <div className="mt-1 flex flex-col gap-3 border-t pt-3">
      <Notice message={plan.note} />

      <div>
        <h4 className="text-[12.5px] font-semibold">단계 {plan.stages.length}개</h4>
        {/*  The chain as text, in order, because that is the whole of the
             draft and a canvas for four nodes would say less. */}
        <p className="mt-1 font-mono text-[12.5px]">{plan.stages.join(' → ')}</p>
      </div>

      {Object.keys(plan.routed_request).length > 0 && (
        <div>
          <h4 className="text-[12.5px] font-semibold">문장에서 읽어낸 조건</h4>
          <ul className="mt-1 flex flex-col gap-0.5">
            {Object.entries(plan.routed_request).map(([k, v]) => (
              <li key={k} className="font-mono text-[12px]">
                {k} = {JSON.stringify(v)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {required.length > 0 && (
        <div>
          <h4 className="text-[12.5px] font-semibold">먼저 있어야 하는 것</h4>
          <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
            {required.map((q) => (
              <li key={q.id} className="text-[12.5px]">
                <code className="font-mono">{q.id}</code> — {q.question}
              </li>
            ))}
          </ul>
        </div>
      )}

      {optional.length > 0 && (
        <details>
          <summary className="cursor-pointer text-[12.5px] font-semibold">
            정하지 않으면 기본값으로 가는 것 {optional.length}개
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 pl-1">
            {optional.map((q) => (
              <li key={q.id} className="text-muted-foreground text-[12px]">
                <code className="font-mono">{q.id}</code> — {q.question}
                {q.default !== undefined && ` (기본 ${JSON.stringify(q.default)})`}
              </li>
            ))}
          </ul>
        </details>
      )}

      {plan.errors.length > 0 && (
        <ErrorBox message={plan.errors.join(' · ')} />
      )}

      <div>
        <Button
          size="sm"
          disabled={!onOpen}
          onClick={() => onOpen?.(plan)}
          title={
            plan.ready
              ? '스튜디오에서 열어 손보고 저장합니다'
              : '빠진 입력은 스튜디오에서 채울 수 있습니다'
          }
        >
          스튜디오에서 열기
        </Button>
        {!plan.ready && (
          <span className="text-muted-foreground ml-2 text-[12px]">
            빠진 입력이 있어 이대로는 실행되지 않습니다.
          </span>
        )}
      </div>
    </div>
  )
}
