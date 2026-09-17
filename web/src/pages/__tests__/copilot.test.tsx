/**
 * The copilot screen.
 *
 * The model is not here. What is under test is that a question is sent with
 * the scope the person can see, that the answer arrives with what it was
 * grounded in, and that a model that is not there is said to be not there.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { Copilot } from '../Copilot'

function show(available = true) {
  vi.spyOn(api, 'copilotStatus').mockResolvedValue({
    available, model: 'exaone3.5:7.8b', url: 'http://127.0.0.1:11434/v1', served: [],
  })
  render(
    <MemoryRouter>
      <Copilot />
    </MemoryRouter>,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('설계 Copilot', () => {
  it('어떤 모델이 답하는지 말한다', async () => {
    show()
    expect(await screen.findByText(/로컬 모델 exaone3.5:7.8b/)).toBeInTheDocument()
  })

  it('모델이 없으면 없다고 말하고 묻지 못하게 한다', async () => {
    show(false)
    expect(await screen.findByText(/모델에 닿지 못했습니다/)).toBeInTheDocument()
    expect(screen.getByLabelText('질문')).toBeDisabled()
  })

  it('물으면 답과 근거가 함께 온다', async () => {
    const chat = vi.spyOn(api, 'copilotChat').mockResolvedValue({
      reply: '아직 실행이 없습니다.', context_used: ['최근 실행 0건', '워크플로 3종'], model: 'exaone3.5:7.8b',
    })
    show()
    await screen.findByText(/로컬 모델/)
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '최근 실행 요약' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    await waitFor(() => expect(screen.getByText('아직 실행이 없습니다.')).toBeInTheDocument())
    expect(screen.getByText(/참고: 최근 실행 0건 · 워크플로 3종/)).toBeInTheDocument()
    expect(chat).toHaveBeenCalledWith(expect.objectContaining({
      messages: [{ role: 'user', content: '최근 실행 요약' }],
    }))
  })

  it('실행 식별자를 적으면 범위에 넣어 보낸다', async () => {
    const chat = vi.spyOn(api, 'copilotChat').mockResolvedValue({ reply: 'x', context_used: [], model: 'm' })
    show()
    await screen.findByText(/로컬 모델/)
    fireEvent.change(screen.getByLabelText('실행 식별자 (선택)'), { target: { value: 'run-9' } })
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '어떻게 됐어' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    await waitFor(() => expect(chat).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-9' })))
  })

  it('답이 실패하면 질문을 되돌려 놓는다', async () => {
    vi.spyOn(api, 'copilotChat').mockRejectedValue(new Error('모델에 닿지 못했습니다'))
    show()
    await screen.findByText(/로컬 모델/)
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '질문' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    await waitFor(() => expect(screen.getByText(/모델에 닿지 못했습니다/)).toBeInTheDocument())
    expect(screen.getByLabelText('질문')).toHaveValue('질문')
  })
})
