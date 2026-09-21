/**
 * 품질 신호 패널.
 *
 * 판정은 원본 agent_panel 의 것이고 이 패널은 그리기만 한다. 여기서 보는
 * 것은 **근거가 함께 보이는가**다. 확인할 수 없는 경고는 믿거나 무시하는
 * 수밖에 없고, 둘 다 없느니만 못하다.
 */

import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type QualityReport, type QualitySignal } from '../../api/client'
import { QualityPanel } from '../QualityPanel'

function signal(over: Partial<QualitySignal> = {}): QualitySignal {
  return {
    stage: 'af2',
    level: 'warning',
    message: '평균 pLDDT 가 낮습니다 (61.2).',
    advice: '구조 예측을 그대로 믿기 어렵습니다.',
    evidence: { plddt: 61.2, threshold: 75 },
    source: 'agent_panel._interpret_af2',
    ...over,
  }
}

function report(signals: QualitySignal[]): QualityReport {
  const counts: Record<string, number> = { info: 0, warning: 0, error: 0 }
  for (const s of signals) counts[s.level] += 1
  return { run_id: 'run-0001', signals, counts, recorded_events: [] }
}

beforeEach(() => vi.restoreAllMocks())

describe('품질 신호', () => {
  it('판정과 권고를 함께 낸다', async () => {
    vi.spyOn(api, 'quality').mockResolvedValue(report([signal()]) as never)

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText('평균 pLDDT 가 낮습니다 (61.2).')).toBeInTheDocument()
    expect(screen.getByText('구조 예측을 그대로 믿기 어렵습니다.')).toBeInTheDocument()
  })

  it('근거가 되는 값과 기준을 보여준다', async () => {
    //  읽는 사람이 판정에 반대할 수 있어야 한다.
    vi.spyOn(api, 'quality').mockResolvedValue(report([signal()]) as never)

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText(/plddt=61\.2/)).toBeInTheDocument()
    expect(screen.getByText(/threshold=75/)).toBeInTheDocument()
  })

  it('판정 기준이 원본의 어느 함수인지 밝힌다', async () => {
    vi.spyOn(api, 'quality').mockResolvedValue(report([signal()]) as never)

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText(/agent_panel\._interpret_af2/)).toBeInTheDocument()
  })

  it('우리 판단에는 원본 출처를 달지 않는다', async () => {
    vi.spyOn(api, 'quality').mockResolvedValue(
      report([signal({ source: 'foldfront', message: '단계가 실패했습니다.' })]) as never,
    )

    render(<QualityPanel runId="run-0001" />)

    await screen.findByText('단계가 실패했습니다.')
    expect(screen.queryByText(/판정 기준/)).not.toBeInTheDocument()
  })

  it('심각도를 세어 머리말에 낸다', async () => {
    vi.spyOn(api, 'quality').mockResolvedValue(
      report([signal({ level: 'error' }), signal(), signal({ level: 'info' })]) as never,
    )

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText('중대 1 · 주의 1 · 참고 1')).toBeInTheDocument()
  })

  it('말할 것이 없으면 왜 없는지 밝힌다', async () => {
    vi.spyOn(api, 'quality').mockResolvedValue(report([]) as never)

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText(/판정할 지표가 아직 없는 단계는 말하지 않습니다/)).toBeInTheDocument()
  })

  it('조회에 실패하면 사유를 보여준다', async () => {
    vi.spyOn(api, 'quality').mockRejectedValue(new Error('실행을 찾지 못했습니다'))

    render(<QualityPanel runId="run-0001" />)

    expect(await screen.findByText('실행을 찾지 못했습니다')).toBeInTheDocument()
  })
})
