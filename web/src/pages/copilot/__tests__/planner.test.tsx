/**
 * 계획 생성 패널.
 *
 * 문장을 읽는 일은 원본 라우터의 것이다. 여기서 보는 것은 **초안이 실행이
 * 아니라는 것을 화면이 분명히 하는가**다.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type Plan } from '../../../api/client'
import { Planner } from '../Planner'

function plan(over: Partial<Plan> = {}): Plan {
  return {
    prompt: 'rfd3 로 백본을 만들어 주십시오',
    routed_request: { rfd3_contig: 'A1-100' },
    missing: [],
    questions: [],
    errors: [],
    stages: ['msa', 'rfd3', 'design', 'soluprot', 'af2'],
    workflow: {
      workflow_id: '',
      version: 1,
      name: '계획 초안',
      nodes: [],
      edges: [],
      is_template: false,
    } as never,
    ready: true,
    defaulted: false,
    note: '초안을 만들었습니다.',
    ...over,
  }
}

beforeEach(() => vi.restoreAllMocks())

describe('계획 생성', () => {
  it('문장을 보내 단계를 순서대로 낸다', async () => {
    const call = vi.spyOn(api, 'copilotPlan').mockResolvedValue(plan() as never)
    render(<Planner />)

    fireEvent.change(screen.getByLabelText('무엇을 하시겠습니까'), {
      target: { value: 'rfd3 로 백본을 만들어 주십시오' },
    })
    fireEvent.click(screen.getByRole('button', { name: '초안 만들기' }))

    expect(await screen.findByText('msa → rfd3 → design → soluprot → af2')).toBeInTheDocument()
    expect(call).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: 'rfd3 로 백본을 만들어 주십시오' }),
    )
  })

  it('문장에서 읽어낸 조건을 보여준다', async () => {
    vi.spyOn(api, 'copilotPlan').mockResolvedValue(plan() as never)
    render(<Planner />)

    fireEvent.click(screen.getByRole('button', { name: 'RFD3 백본 설계' }))

    expect(await screen.findByText(/rfd3_contig = "A1-100"/)).toBeInTheDocument()
  })

  it('빠진 입력이 있으면 실행되지 않는다고 말한다', async () => {
    //  기본값으로 채워 시작하면 추측에 GPU 시간을 쓴다.
    vi.spyOn(api, 'copilotPlan').mockResolvedValue(
      plan({
        ready: false,
        missing: ['rfd3_input_pdb'],
        questions: [{ id: 'rfd3_input_pdb', question: 'Provide rfd3_input_pdb text.', required: true }],
        note: '필요한 입력이 아직 없습니다.',
      }) as never,
    )
    render(<Planner />)

    fireEvent.click(screen.getByRole('button', { name: 'RFD3 백본 설계' }))

    expect(await screen.findByText(/이대로는 실행되지 않습니다/)).toBeInTheDocument()
    expect(screen.getByText('rfd3_input_pdb')).toBeInTheDocument()
  })

  it('기본값으로 가는 항목은 접어 둔다', async () => {
    vi.spyOn(api, 'copilotPlan').mockResolvedValue(
      plan({
        questions: [
          { id: 'stop_after', question: 'Where to stop?', required: false, default: 'novelty' },
        ],
      }) as never,
    )
    render(<Planner />)

    fireEvent.click(screen.getByRole('button', { name: '정형 체인 전체' }))

    expect(await screen.findByText(/기본값으로 가는 것 1개/)).toBeInTheDocument()
  })

  it('조건을 읽지 못했으면 기본 체인이라고 밝힌다', async () => {
    //  기본 체인을 라우터의 판단처럼 보이게 하지 않는다.
    vi.spyOn(api, 'copilotPlan').mockResolvedValue(
      plan({
        routed_request: {},
        defaulted: true,
        note: '문장에서 읽어낸 조건이 없어 정형 단계 체인을 그대로 냈습니다.',
      }) as never,
    )
    render(<Planner />)

    fireEvent.click(screen.getByRole('button', { name: '정형 체인 전체' }))

    expect(await screen.findByText(/읽어낸 조건이 없어/)).toBeInTheDocument()
  })

  it('스튜디오로 초안을 넘긴다', async () => {
    vi.spyOn(api, 'copilotPlan').mockResolvedValue(plan() as never)
    const onOpen = vi.fn()
    render(<Planner onOpen={onOpen} />)

    fireEvent.click(screen.getByRole('button', { name: 'RFD3 백본 설계' }))
    fireEvent.click(await screen.findByRole('button', { name: '스튜디오에서 열기' }))

    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ ready: true }))
  })

  it('빈 문장으로는 묻지 않는다', () => {
    const call = vi.spyOn(api, 'copilotPlan')
    render(<Planner />)

    expect(screen.getByRole('button', { name: '초안 만들기' })).toBeDisabled()
    expect(call).not.toHaveBeenCalled()
  })

  it('실패하면 사유를 보여준다', async () => {
    vi.spyOn(api, 'copilotPlan').mockRejectedValue(new Error('원본 라우터를 쓸 수 없습니다'))
    render(<Planner />)

    fireEvent.click(screen.getByRole('button', { name: '정형 체인 전체' }))

    expect(await screen.findByText('원본 라우터를 쓸 수 없습니다')).toBeInTheDocument()
  })
})
