/**
 * 콘솔 화면 시험.
 *
 * API 는 가짜로 세우고 화면이 응답을 바르게 그리는지만 본다.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../../api/client'
import { Monitor } from '../Monitor'
import { Models } from '../Models'
import { Analyze } from '../Analyze'
import { duration, formatBytes, formatTime } from '../../components/Common'

function run(overrides: Partial<Parameters<typeof Object.assign>[0]> = {}) {
  return {
    run_id: 'run-0001',
    status: 'succeeded',
    mode: 'workflow',
    project_id: null,
    round_id: null,
    request: {},
    stages: [
      {
        name: 'msa',
        status: 'succeeded',
        started_at: '2026-09-16T01:00:00Z',
        finished_at: '2026-09-16T01:10:00Z',
        model_id: 'mmseqs',
        model_version: 'v2',
        error: null,
        metrics: { depth: 120, _mock: true },
      },
      {
        name: 'af2',
        status: 'failed',
        started_at: null,
        finished_at: null,
        model_id: 'af2',
        model_version: null,
        error: 'GPU 없음',
        metrics: {},
      },
    ],
    forked_from_run_id: null,
    forked_from_stage: null,
    workflow_id: 'builtin-pipeline',
    workflow_version: 1,
    created_at: '2026-09-16T01:00:00Z',
    started_at: '2026-09-16T01:00:00Z',
    finished_at: '2026-09-16T01:20:00Z',
    error: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('보조 함수', () => {
  it('바이트를 단위와 함께 낸다', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 ** 2)).toBe('5.0 MB')
  })

  it('걸린 시간을 사람이 읽는 형태로 낸다', () => {
    expect(duration('2026-09-16T01:00:00Z', '2026-09-16T01:00:30Z')).toBe('30초')
    expect(duration('2026-09-16T01:00:00Z', '2026-09-16T01:05:10Z')).toBe('5분 10초')
    expect(duration('2026-09-16T01:00:00Z', '2026-09-16T03:30:00Z')).toBe('2시간 30분')
  })

  it('값이 없으면 줄표를 낸다', () => {
    expect(duration(null, null)).toBe('—')
    expect(formatTime(null)).toBe('—')
    expect(formatTime('말이 안 되는 값')).toBe('—')
  })
})

describe('Monitor', () => {
  it('실행 목록과 큐 현황을 그린다', async () => {
    vi.spyOn(api, 'listRuns').mockResolvedValue({ items: [run()], count: 1 } as never)
    vi.spyOn(api, 'jobStats').mockResolvedValue({
      by_status: { queued: 2, succeeded: 5 },
      total: 7,
    } as never)

    render(<MemoryRouter><Monitor /></MemoryRouter>)

    expect(await screen.findByText('run-0001')).toBeInTheDocument()
    expect(screen.getByText('builtin-pipeline')).toBeInTheDocument()
    //  단계 2개 중 1개 성공
    expect(screen.getByText('1/2')).toBeInTheDocument()
    //  큐 합계
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('실행이 없으면 빈 안내를 낸다', async () => {
    vi.spyOn(api, 'listRuns').mockResolvedValue({ items: [], count: 0 } as never)
    vi.spyOn(api, 'jobStats').mockResolvedValue({ by_status: {}, total: 0 } as never)

    render(<MemoryRouter><Monitor /></MemoryRouter>)

    expect(await screen.findByText('실행 기록이 없습니다.')).toBeInTheDocument()
  })

  it('조회에 실패하면 사유를 보여준다', async () => {
    vi.spyOn(api, 'listRuns').mockRejectedValue(new Error('연결할 수 없습니다'))
    vi.spyOn(api, 'jobStats').mockResolvedValue({ by_status: {}, total: 0 } as never)

    render(<MemoryRouter><Monitor /></MemoryRouter>)

    expect(await screen.findByText('연결할 수 없습니다')).toBeInTheDocument()
  })
})

describe('Model Registry', () => {
  it('모델 목록과 상태를 그린다', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({
      items: [
        {
          model_id: 'rfd3',
          version: '2026-09-01',
          kind: 'backbone',
          display_name: null,
          endpoint_id: 'ep-rfd3',
          base_url: null,
          container_image: null,
          resources: { gpu_count: 1, gpu_memory_gb: 24, timeout_seconds: 21600 },
          active: true,
          is_default: true,
          approval_status: 'approved',
        },
        {
          model_id: 'custom',
          version: 'v1',
          kind: 'other',
          display_name: null,
          endpoint_id: null,
          base_url: 'http://127.0.0.1:9000',
          container_image: null,
          resources: { gpu_count: 0, gpu_memory_gb: null, timeout_seconds: 21600 },
          active: false,
          is_default: false,
          approval_status: 'pending',
        },
      ],
      count: 2,
    } as never)

    render(<MemoryRouter><Models /></MemoryRouter>)

    expect(await screen.findByText('rfd3')).toBeInTheDocument()
    expect(screen.getByText('ep-rfd3')).toBeInTheDocument()
    expect(screen.getByText('기본')).toBeInTheDocument()

    //  「비활성」은 배지와 단추 양쪽에 나오므로 행 안에서 찾는다
    const customRow = screen.getByText('custom').closest('tr')!
    expect(within(customRow).getByText('비활성', { selector: '.badge' })).toBeInTheDocument()
    expect(within(customRow).getByText('대기')).toBeInTheDocument()
    //  승인 대기인 것에만 승인 단추가 보인다
    expect(within(customRow).getByRole('button', { name: '승인' })).toBeInTheDocument()

    const rfd3Row = screen.getByText('rfd3').closest('tr')!
    expect(within(rfd3Row).getByText('활성', { selector: '.badge' })).toBeInTheDocument()
    expect(within(rfd3Row).queryByRole('button', { name: '승인' })).not.toBeInTheDocument()
  })

  it('실행 위치가 없으면 줄표를 낸다', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({
      items: [{
        model_id: 'empty', version: 'v1', kind: 'other', display_name: null,
        endpoint_id: null, base_url: null, container_image: null,
        resources: { gpu_count: 0, gpu_memory_gb: null, timeout_seconds: 21600 },
        active: true, is_default: false, approval_status: 'approved',
      }],
      count: 1,
    } as never)

    render(<MemoryRouter><Models /></MemoryRouter>)

    const row = (await screen.findByText('empty')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
  })
})

describe('Analyze', () => {
  it('지표 요약 표에 단계별 값을 펼친다', async () => {
    vi.spyOn(api, 'listRuns').mockResolvedValue({ items: [run()], count: 1 } as never)

    render(<MemoryRouter><Analyze /></MemoryRouter>)

    //  msa.depth 가 열로 잡히고 값 120 이 들어간다. 내부 표기(_mock)는 제외한다
    await waitFor(() => expect(screen.getByText('msa.depth')).toBeInTheDocument())
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.queryByText('msa._mock')).not.toBeInTheDocument()
  })

  it('비교 대상을 고르기 전에는 안내를 낸다', async () => {
    vi.spyOn(api, 'listRuns').mockResolvedValue({ items: [run()], count: 1 } as never)

    render(<MemoryRouter><Analyze /></MemoryRouter>)

    expect(await screen.findByText('비교할 실행 둘을 고르십시오.')).toBeInTheDocument()
  })
})

//  ---------------------------------------------------------------- 구조 열람
//  WebGL 은 jsdom 에서 돌지 않는다. 뷰어 자체가 아니라 「어떤 산출물을 뷰어에 넘기는가」를 본다.

describe('구조 산출물', () => {
  it('산출물 주소에 경로를 실어 만든다', () => {
    const url = api.artifactUrl('run-0001', 'run-0001/af2/best design.pdb')
    expect(url).toBe(
      '/api/v1/runs/run-0001/artifacts/content?path=run-0001%2Faf2%2Fbest+design.pdb',
    )
  })

  it('PDB 산출물에만 구조 열람을 붙인다', async () => {
    vi.spyOn(api, 'listRuns').mockResolvedValue({ items: [run()], count: 1 } as never)
    vi.spyOn(api, 'jobStats').mockResolvedValue({ by_status: {}, total: 0 } as never)
    vi.spyOn(api, 'getRun').mockResolvedValue(run() as never)
    vi.spyOn(api, 'listEvents').mockResolvedValue({ items: [], count: 0 } as never)
    vi.spyOn(api, 'listArtifacts').mockResolvedValue({
      items: [
        { run_id: 'run-0001', stage: 'af2', path: 'run-0001/af2/best.pdb', kind: 'pdb', size_bytes: 10, user_visible: true },
        { run_id: 'run-0001', stage: 'msa', path: 'run-0001/msa/hits.a3m', kind: 'a3m', size_bytes: 20, user_visible: true },
      ],
      count: 2,
      total_bytes: 30,
    } as never)

    render(<MemoryRouter><Monitor /></MemoryRouter>)
    fireEvent.click(await screen.findByText('run-0001'))

    const pdbRow = (await screen.findByText('run-0001/af2/best.pdb')).closest('tr')!
    expect(within(pdbRow).getByRole('button', { name: '구조 열람' })).toBeInTheDocument()

    const a3mRow = screen.getByText('run-0001/msa/hits.a3m').closest('tr')!
    expect(within(a3mRow).queryByRole('button', { name: '구조 열람' })).not.toBeInTheDocument()
  })
})

//  ---------------------------------------------------------------- API errors
//  The platform answers a code plus a translated message. A screen must show the
//  message and be able to branch on the code without matching on the text.

describe('API 오류', () => {
  function respond(status: number, body: unknown) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status,
      statusText: 'Not Found',
      json: async () => body,
    } as never))
  }

  it('코드와 메시지와 인자를 모두 싣는다', async () => {
    respond(404, {
      error: { code: 'run.not_found', message: '실행을 찾지 못했습니다: run-1', params: { run_id: 'run-1' } },
    })

    const caught = (await api.getRun('run-1').catch((e) => e)) as ApiError

    expect(caught).toBeInstanceOf(ApiError)
    expect(caught.code).toBe('run.not_found')
    expect(caught.message).toBe('실행을 찾지 못했습니다: run-1')
    expect(caught.params).toEqual({ run_id: 'run-1' })
    expect(caught.status).toBe(404)
  })

  it('FastAPI 자체 오류인 detail 형태도 읽는다', async () => {
    respond(422, { detail: 'validation failed' })

    const caught = (await api.getRun('run-1').catch((e) => e)) as ApiError

    expect(caught.message).toBe('validation failed')
    expect(caught.code).toBeNull()
  })

  it('본문이 JSON 이 아니면 상태줄을 쓴다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 502, statusText: 'Bad Gateway',
      json: async () => { throw new Error('not json') },
    } as never))

    const caught = (await api.getRun('run-1').catch((e) => e)) as ApiError

    expect(caught.message).toBe('502 Bad Gateway')
  })
})
