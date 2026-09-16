/**
 * The project selection.
 *
 * What matters is not that a dropdown opens, but that one choice governs
 * every screen and survives a reload - and that it fails towards showing too
 * much rather than too little, because a screen that hides runs looks broken
 * in exactly the same way as a screen with no runs.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type Project } from '../../api/client'
import { ProjectPicker } from '../ProjectPicker'
import { ProjectProvider, roundLabel, useProject } from '@/lib/project'

function project(over: Partial<Project> = {}): Project {
  return {
    project_id: 'proj-a',
    name: '리소자임 용해도',
    description: null,
    owner_id: null,
    archived: false,
    tags: [],
    ...over,
  }
}

function stub(projects: Project[]) {
  vi.spyOn(api, 'listProjects').mockResolvedValue({
    items: projects,
    count: projects.length,
  } as never)
  vi.spyOn(api, 'listRounds').mockResolvedValue({ items: [], count: 0 } as never)
}

function show(projects: Project[]) {
  stub(projects)
  render(
    <ProjectProvider>
      <ProjectPicker />
    </ProjectProvider>,
  )
}

/** Reports what the rest of the console would be asked to filter by. */
function Probe() {
  const { filter, current } = useProject()
  return <span data-testid="probe">{`${current?.name ?? '없음'}|${filter ?? '전체'}`}</span>
}

beforeEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('프로젝트 선택기', () => {
  it('프로젝트가 없으면 아예 내지 않는다', async () => {
    show([])
    //  Nothing to choose between is not a choice worth offering.
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /프로젝트/ })).not.toBeInTheDocument()
  })

  it('고르기 전에는 전체를 낸다', async () => {
    show([project()])
    expect(await screen.findByRole('button', { name: /전체/ })).toBeInTheDocument()
  })

  it('목록에 전체와 프로젝트를 함께 낸다', async () => {
    show([project(), project({ project_id: 'proj-b', name: '결합 친화도' })])
    fireEvent.click(await screen.findByRole('button', { name: /프로젝트/ }))

    const menu = screen.getByRole('menu', { name: '프로젝트 선택' })
    expect(within(menu).getByRole('menuitemradio', { name: /전체/ })).toBeChecked()
    expect(within(menu).getByRole('menuitemradio', { name: /리소자임/ })).toBeInTheDocument()
    expect(within(menu).getByRole('menuitemradio', { name: /결합 친화도/ })).toBeInTheDocument()
  })

  it('고르면 머리말이 그 프로젝트를 말한다', async () => {
    show([project()])
    fireEvent.click(await screen.findByRole('button', { name: /프로젝트/ }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /리소자임/ }))

    expect(screen.getByRole('button', { name: /리소자임/ })).toBeInTheDocument()
  })

  it('Escape 로 닫는다', async () => {
    show([project()])
    fireEvent.click(await screen.findByRole('button', { name: /프로젝트/ }))
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})

describe('프로젝트 조회 범위', () => {
  it('고르지 않으면 거르지 않는다', async () => {
    stub([project()])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    expect(screen.getByTestId('probe')).toHaveTextContent('없음|전체')
  })

  it('고른 프로젝트를 다음에도 기억한다', async () => {
    window.localStorage.setItem('foldfront.project', 'proj-a')
    stub([project()])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('리소자임 용해도|proj-a'))
  })

  it('기억한 프로젝트가 사라졌으면 전체로 돌아간다', async () => {
    //  Otherwise every screen filters on an identifier nothing matches, and
    //  the console looks empty rather than unfiltered.
    window.localStorage.setItem('foldfront.project', 'proj-gone')
    stub([project()])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    expect(screen.getByTestId('probe')).toHaveTextContent('없음|전체')
  })

  it('프로젝트를 읽지 못해도 화면은 전체로 뜬다', async () => {
    vi.spyOn(api, 'listProjects').mockRejectedValue(new Error('끊김'))
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('없음|전체'))
  })
})

describe('회차 이름', () => {
  const round = (over = {}) => ({
    round_id: 'round-1',
    project_id: 'proj-a',
    name: null,
    index: 2,
    linked_run_ids: [],
    objective: null,
    archived: false,
    ...over,
  })

  it('이름이 없으면 몇 차인지로 부른다', () => {
    expect(roundLabel(round())).toBe('2차 설계')
  })

  it('이름이 있으면 그 이름을 쓴다', () => {
    expect(roundLabel(round({ name: '소수성 감소' }))).toBe('소수성 감소')
  })

  it('공백뿐인 이름은 이름이 없는 것으로 본다', () => {
    expect(roundLabel(round({ name: '  ' }))).toBe('2차 설계')
  })
})

describe('주소로 프로젝트 열기', () => {
  function at(search: string) {
    window.history.replaceState({}, '', `/dashboard${search}`)
  }

  it('주소가 이름한 프로젝트로 연다', async () => {
    at('?project=proj-a')
    stub([project()])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('proj-a'))
  })

  it('주소가 기억한 것을 이긴다', async () => {
    //  A link is a deliberate act; what the browser remembered is not.
    window.localStorage.setItem('foldfront.project', 'proj-b')
    at('?project=proj-a')
    stub([project(), project({ project_id: 'proj-b', name: '결합 친화도' })])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('proj-a'))
  })

  it('주소가 모르는 프로젝트를 이름하면 전체로 연다', async () => {
    at('?project=proj-gone')
    stub([project()])
    render(
      <ProjectProvider>
        <Probe />
      </ProjectProvider>,
    )

    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    expect(screen.getByTestId('probe')).toHaveTextContent('없음|전체')
  })
})
