/**
 * The bell.
 *
 * What is under test is restraint: that it counts only what is new, that
 * opening it is reading it, and that an empty list says so rather than
 * showing a zero. A badge that never clears is a badge nobody reads.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type Notice } from '../../api/client'
import { Notices } from '../Notices'

function notice(over: Partial<Notice> = {}): Notice {
  return {
    id: 'run.failed:run-1',
    kind: 'run.failed',
    severity: 'warning',
    title: '실행이 실패했습니다',
    detail: 'run-1 — 설계 서열이 없습니다',
    href: '/monitor',
    at: '2026-09-16T01:00:00Z',
    ...over,
  }
}

function show(items: Notice[]) {
  vi.spyOn(api, 'notices').mockResolvedValue({ items, count: items.length } as never)
  render(
    <MemoryRouter>
      <Notices />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('알림', () => {
  it('아무것도 없으면 숫자를 달지 않는다', async () => {
    show([])
    await waitFor(() => expect(api.notices).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: '알림' })).toBeInTheDocument()
  })

  it('읽지 않은 건수를 단다', async () => {
    show([notice(), notice({ id: 'model.approval:af2:v1' })])
    expect(await screen.findByRole('button', { name: '알림 2건' })).toBeInTheDocument()
  })

  it('열면 내용을 낸다', async () => {
    show([notice()])
    fireEvent.click(await screen.findByRole('button', { name: /알림/ }))

    const menu = screen.getByRole('menu', { name: '알림' })
    expect(within(menu).getByText('실행이 실패했습니다')).toBeInTheDocument()
    expect(within(menu).getByText(/설계 서열이 없습니다/)).toBeInTheDocument()
  })

  it('한 번 열면 읽은 것으로 보고 숫자를 지운다', async () => {
    show([notice()])
    fireEvent.click(await screen.findByRole('button', { name: '알림 1건' }))

    //  Still listed - reading is not dismissing - but no longer counted.
    expect(screen.getByRole('button', { name: '알림' })).toBeInTheDocument()
    expect(screen.getByRole('menu', { name: '알림' })).toBeInTheDocument()
  })

  it('다음에 새로 생긴 것만 다시 센다', async () => {
    window.localStorage.setItem(
      'foldfront.notices.read',
      JSON.stringify(['run.failed:run-1']),
    )
    show([notice(), notice({ id: 'run.failed:run-2' })])

    expect(await screen.findByRole('button', { name: '알림 1건' })).toBeInTheDocument()
  })

  it('열 것이 없으면 없다고 말한다', async () => {
    show([])
    fireEvent.click(await screen.findByRole('button', { name: '알림' }))

    expect(screen.getByText('손을 기다리는 일이 없습니다.')).toBeInTheDocument()
  })

  it('갈 곳이 있는 알림만 누를 수 있다', async () => {
    show([notice(), notice({ id: 'auth.disabled', title: '인증이 꺼져 있습니다', href: null })])
    fireEvent.click(await screen.findByRole('button', { name: /알림/ }))

    const menu = screen.getByRole('menu', { name: '알림' })
    const rows = within(menu).getAllByRole('menuitem')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('실행이 실패했습니다')
  })

  it('아홉 건을 넘으면 9+ 로 줄인다', async () => {
    show(Array.from({ length: 12 }, (_, i) => notice({ id: `run.failed:run-${i}` })))
    expect(await screen.findByText('9+')).toBeInTheDocument()
  })

  it('Escape 로 닫는다', async () => {
    show([notice()])
    fireEvent.click(await screen.findByRole('button', { name: /알림/ }))
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
