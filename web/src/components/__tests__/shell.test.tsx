/**
 * 콘솔 골격 시험.
 *
 * 헤더 · 좌측 내비게이션 · 본문 · 푸터 4분할이 전 화면에서 유지되는지 본다.
 * 화면 내용이 아니라 골격만 본다 — 내용은 화면별 시험이 맡는다.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { Shell, PageHeader } from '../Shell'

beforeEach(() => {
  vi.restoreAllMocks()
  document.documentElement.classList.remove('dark')
  //  기본은 응답하지 않게 둔다 — 상태를 보는 시험에서만 값을 준다.
  //  그러지 않으면 시험이 끝난 뒤에 상태가 갱신되어 act 경고가 난다
  vi.spyOn(api, 'health').mockReturnValue(new Promise(() => {}) as never)
})

function mount(path = '/monitor') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Shell>
        <PageHeader title="실행 감시" description="상태를 확인합니다." />
      </Shell>
    </MemoryRouter>,
  )
}

describe('콘솔 골격', () => {
  it('헤더 · 내비게이션 · 본문 · 푸터를 모두 그린다', async () => {
    const { container } = mount()

    expect(container.querySelector('header')).toBeInTheDocument()
    expect(container.querySelector('nav')).toBeInTheDocument()
    expect(container.querySelector('main')).toBeInTheDocument()
    expect(container.querySelector('footer')).toBeInTheDocument()

    expect(screen.getByText('단백질 설계 자동화 플랫폼')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '실행 감시' })).toBeInTheDocument()
  })

  it('좌측 내비게이션에 대메뉴와 하위 메뉴를 둔다', () => {
    const { container } = mount()
    const nav = container.querySelector('nav')!

    for (const section of ['설계', '관리']) {
      expect(within(nav, section)).toBe(true)
    }
    for (const label of [
      '대시보드',
      '설계 실행',
      '실행 준비',
      '실행 감시',
      '워크플로 스튜디오',
      '결과 분석',
      '모델 관리',
      '운영',
      '설계 Copilot',
    ]) {
      expect(within(nav, label)).toBe(true)
    }
  })

  it('미구현 메뉴는 링크가 아니라 「예정」으로 표시한다', () => {
    const { container } = mount()
    const nav = container.querySelector('nav')!
    const hrefs = Array.from(nav.querySelectorAll('a')).map((a) => a.getAttribute('href'))

    expect(hrefs).toContain('/monitor')
    expect(hrefs).not.toContain('/copilot')
    expect(nav.textContent).toContain('예정')
  })

  it('현재 경로의 메뉴만 활성 표시한다', () => {
    const { container } = mount('/models')
    const active = Array.from(container.querySelectorAll('nav a.bg-accent'))

    expect(active).toHaveLength(1)
    expect(active[0].getAttribute('href')).toBe('/models')
  })

  it('푸터에 판번호와 기준 커밋을 낸다', () => {
    const { container } = mount()
    const footer = container.querySelector('footer')!

    expect(footer.textContent).toContain(__APP_VERSION__)
    expect(footer.textContent).toContain(__APP_COMMIT__)
    expect(footer.textContent).toContain('RAPID v1.0.29')
  })

  it('저장소 연결 상태를 실제 /healthz 로 확인한다', async () => {
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', mongo_ok: true } as never)
    mount()
    await waitFor(() => expect(api.health).toHaveBeenCalled())
    expect(await screen.findByText('정상')).toBeInTheDocument()
  })

  it('어두운 화면으로 전환하면 문서 뿌리에 표시가 붙는다', async () => {
    mount()

    expect(document.documentElement.classList.contains('dark')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '어두운 화면으로' }))
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true))
  })
})

function within(root: Element, text: string): boolean {
  return (root.textContent ?? '').includes(text)
}
