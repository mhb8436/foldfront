import { api, type QualitySignal } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { Empty, ErrorBox, Panel } from './Common'

/**
 * What the run's numbers say, and what to do about it.
 *
 * The judgements are the original's - the thresholds come from
 * `pipeline_mcp/agent_panel.py` and each signal carries the function it came
 * from. This renders them; it decides nothing.
 *
 * Every signal shows its evidence, because a warning a reader cannot check
 * is a warning they have to either trust or ignore, and both are worse than
 * being able to look. "평균 pLDDT 가 낮습니다 (61.2)" beside "기준 75.0" lets
 * someone disagree with the threshold rather than with the software.
 *
 * Severity is shown by rule weight and a word, not by colour: the palette
 * carries run status and nothing else, and these panels are read on paper.
 */

const LEVEL_LABEL: Record<string, string> = {
  error: '중대',
  warning: '주의',
  info: '참고',
}

/** Heavier rule for worse news. Reads the same in black and white. */
const LEVEL_RULE: Record<string, string> = {
  error: 'border-l-[3px] border-l-foreground',
  warning: 'border-l-2 border-l-foreground/60',
  info: 'border-l border-l-muted-foreground/40',
}

function evidence(signal: QualitySignal): string | null {
  const entries = Object.entries(signal.evidence ?? {}).filter(
    ([, v]) => v !== null && v !== undefined,
  )
  if (entries.length === 0) return null
  return entries.map(([k, v]) => `${k}=${String(v)}`).join(' · ')
}

export function QualityPanel({ runId }: { runId: string }) {
  const report = useAsync(() => api.quality(runId), [runId])

  const signals = report.data?.signals ?? []
  const counts = report.data?.counts ?? {}
  const summary = ['error', 'warning', 'info']
    .filter((level) => (counts[level] ?? 0) > 0)
    .map((level) => `${LEVEL_LABEL[level]} ${counts[level]}`)
    .join(' · ')

  return (
    <Panel title="품질 신호" description={summary || undefined}>
      <ErrorBox message={report.error} />
      {signals.length === 0 ? (
        <Empty>
          {report.loading
            ? '불러오는 중입니다.'
            : '지적할 것이 없습니다. 판정할 지표가 아직 없는 단계는 말하지 않습니다.'}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {signals.map((s, i) => (
            <li key={`${s.stage}:${i}`} className={`py-0.5 pl-3 ${LEVEL_RULE[s.level] ?? ''}`}>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[11px] font-semibold tracking-wide uppercase">
                  {LEVEL_LABEL[s.level] ?? s.level}
                </span>
                <code className="font-mono text-[12px]">{s.stage}</code>
                <span className="text-[13.5px]">{s.message}</span>
              </div>
              {s.advice && (
                <p className="text-muted-foreground mt-0.5 text-[12.5px]">{s.advice}</p>
              )}
              {/*  The reader can check the judgement rather than take it. */}
              <p className="text-muted-foreground mt-0.5 font-mono text-[11.5px]">
                {evidence(s)}
                {s.source && s.source !== 'foldfront' && (
                  <span className="ml-2">· 판정 기준 {s.source}</span>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
