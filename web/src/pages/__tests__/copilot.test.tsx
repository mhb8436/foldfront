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

beforeEach(() => {
  vi.restoreAllMocks()
  window.sessionStorage.clear()
})

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
    fireEvent.change(screen.getByLabelText('실행 식별자 (선택)'), { target: { value: 'run-9abc12' } })
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '어떻게 됐어' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    await waitFor(() => expect(chat).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-9abc12' })))
  })

  it('답이 실패하면 질문을 되돌려 놓는다', async () => {
    vi.spyOn(api, 'copilotChat').mockRejectedValue(new Error('모델에 닿지 못했습니다'))
    show()
    await screen.findByText(/로컬 모델/)
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '질문' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    await waitFor(() => expect(screen.getByText(/모델에 닿지 못했습니다/)).toBeInTheDocument())
    expect(screen.getByLabelText('질문')).toHaveValue('질문')
    //  and the question is not left in the thread as if it had been answered
    expect(screen.queryByText('질문', { selector: 'div' })).not.toBeInTheDocument()
  })

  it('실행 식별자 형식이 아니면 보내기 전에 말한다', async () => {
    const chat = vi.spyOn(api, 'copilotChat')
    show()
    await screen.findByText(/로컬 모델/)
    fireEvent.change(screen.getByLabelText('실행 식별자 (선택)'), { target: { value: 'lysozyme' } })
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '어떻게 됐어' } })
    fireEvent.click(screen.getByRole('button', { name: /보내기/ }))

    expect(await screen.findByText(/실행 식별자 형식이 아닙니다/)).toBeInTheDocument()
    expect(chat).not.toHaveBeenCalled()
  })

  it('대화는 다른 화면에 다녀와도 남는다', async () => {
    window.sessionStorage.setItem('foldfront.copilot', JSON.stringify([
      { role: 'user', content: '앞선 질문' }, { role: 'assistant', content: '앞선 답', context: ['최근 실행 1건'] },
    ]))
    show()
    expect(await screen.findByText('앞선 답')).toBeInTheDocument()
    expect(screen.getByText(/참고: 최근 실행 1건/)).toBeInTheDocument()
  })

  it('상태 자체를 못 읽으면 그렇다고 말한다', async () => {
    vi.spyOn(api, 'copilotStatus').mockRejectedValue(new Error('끊김'))
    render(
      <MemoryRouter>
        <Copilot />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/상태를 읽지 못했습니다 — 끊김/)).toBeInTheDocument()
  })
})
