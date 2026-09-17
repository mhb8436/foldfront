/**
 * The console shell.
 *
 * That the four regions - header, navigation, content, footer - hold across
 * screens. Contents belong to the per-screen tests; this is about the frame.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { IdentityProvider } from '../../lib/identity'
import { Shell, PageHeader } from '../Shell'

beforeEach(() => {
  vi.restoreAllMocks()
  document.documentElement.classList.remove('dark')
  //  Never resolves by default; only the health test supplies a value.
  //  Otherwise the state lands after the test ends and React warns about it.
  vi.spyOn(api, 'health').mockReturnValue(new Promise(() => {}) as never)
})

function mount(path = '/monitor', roles = ['admin']) {
  vi.spyOn(api, 'me').mockResolvedValue({
    user_id: 'tester', email: '', roles, authenticated: true, auth_mode: 'oidc',
  } as never)
  return render(
    <MemoryRouter initialEntries={[path]}>
      <IdentityProvider>
        <Shell>
          <PageHeader title="실행 감시" description="상태를 확인합니다." />
        </Shell>
      </IdentityProvider>
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

  it('좌측 내비게이션에 대메뉴와 하위 메뉴를 둔다', async () => {
    const { container } = mount()
    await screen.findByText('tester')
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

  it('모든 메뉴가 실제 화면으로 이어진다', async () => {
    //  There was a 예정 badge on the copilot until it existed. Nothing in the
    //  navigation should promise a screen that is not there.
    const { container } = mount()
    await screen.findByText('tester')
    const nav = container.querySelector('nav')!
    const hrefs = Array.from(nav.querySelectorAll('a')).map((a) => a.getAttribute('href'))

    expect(hrefs).toContain('/monitor')
    expect(hrefs).toContain('/copilot')
    expect(nav.textContent).not.toContain('예정')
  })

  it('현재 경로의 메뉴만 활성 표시한다', async () => {
    const { container } = mount('/models')
    await screen.findByText('tester')
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


describe('권한에 따른 메뉴', () => {
  it('운영자에게는 운영 메뉴가 보인다', async () => {
    const { container } = mount('/monitor', ['admin'])
    await screen.findByText('tester')

    expect(container.querySelector('nav')!.textContent).toContain('운영')
  })

  it('조회자에게는 운영 메뉴를 감춘다', async () => {
    const { container } = mount('/monitor', ['viewer'])
    await screen.findByText('tester')

    expect(container.querySelector('nav')!.textContent).not.toContain('운영')
  })

  it('헤더에 실제 이용자와 역할을 낸다', async () => {
    mount('/monitor', ['researcher'])

    expect(await screen.findByText('tester')).toBeInTheDocument()
    expect(screen.getByText('연구자')).toBeInTheDocument()
  })

  it('인증이 꺼져 있으면 그 사실을 함께 낸다', async () => {
    vi.spyOn(api, 'me').mockResolvedValue({
      user_id: 'dev', email: '', roles: ['admin'], authenticated: false, auth_mode: 'disabled',
    } as never)
    render(
      <MemoryRouter>
        <IdentityProvider>
          <Shell>
            <PageHeader title="x" />
          </Shell>
        </IdentityProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/인증 꺼짐/)).toBeInTheDocument()
  })
})
