/**
 * 후보군 순위표.
 *
 * 표에 나오는 수는 전부 원본이 계산한 것이다. 여기서 보는 것은 값이 아니라
 * **표시**다 — WT 차이가 실리는가, 후보가 없을 때 왜 없는지 말하는가,
 * 가중치를 바꾸면 다시 묻는가.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type HitList as HitListData, type HitRow } from '../../../api/client'
import { HitList } from '../HitList'

function row(over: Partial<HitRow> = {}): HitRow {
  return {
    rank: 1,
    seq_id: 'rfd3_spec-1_0_model_0:187',
    tier: 0.5,
    source: 'proteinmpnn',
    sequence: 'MKT',
    soluprot: 0.7338,
    plddt: 89.524,
    rmsd: 1.21,
    rmsd_target: null,
    relax: -3.15,
    wt_identity: 0.82,
    wt_identity_pct: 82.4,
    wt_diff_count: 31,
    wt_compare_len: 176,
    wt_diff_ratio: 0.176,
    wt_diff_pct: 17.6,
    novelty: 0.176,
    soluprot_passed: true,
    af2_candidate: true,
    af2_selected: false,
    score: 0.813,
    af2_ranked_pdb_path: null,
    ...over,
  }
}

function list(over: Partial<HitListData> = {}): HitListData {
  return {
    run_id: 'run-0001',
    generated_at: '2026-09-21T03:00:00Z',
    weights: { soluprot: 0.4, plddt: 0.3, rmsd: 0.2, novelty: 0.1 },
    min_score: 0,
    rmsd_ref: 5,
    relax_enabled: true,
    total_rows: 1,
    filtered_rows: 1,
    rows: [row()],
    stats: {},
    ...over,
  }
}

beforeEach(() => vi.restoreAllMocks())

describe('후보군 순위', () => {
  it('원본이 낸 점수와 지표를 그대로 싣는다', async () => {
    vi.spyOn(api, 'hitList').mockResolvedValue(list() as never)

    render(<HitList runId="run-0001" />)

    expect(await screen.findByText('0.813')).toBeInTheDocument()
    expect(screen.getByText('0.734')).toBeInTheDocument()   // 용해도
    expect(screen.getByText('89.5')).toBeInTheDocument()    // pLDDT
  })

  it('WT 차이를 잔기 수와 비교 길이로 함께 낸다', async () => {
    //  비율만으로는 31/176 인지 31/31 인지 읽는 사람이 알 수 없다.
    vi.spyOn(api, 'hitList').mockResolvedValue(list() as never)

    render(<HitList runId="run-0001" />)

    const cell = await screen.findByText('31')
    expect(within(cell).getByText('/176')).toBeInTheDocument()
    expect(screen.getByText('82.4%')).toBeInTheDocument()
  })

  it('없는 값은 줄표로 낸다', async () => {
    vi.spyOn(api, 'hitList').mockResolvedValue(
      list({ rows: [row({ wt_diff_count: null, plddt: null })] }) as never,
    )

    render(<HitList runId="run-0001" />)

    await screen.findByText('0.813')
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
  })

  it('후보가 없으면 왜 없는지 말한다', async () => {
    //  빈 표만 내면 후보가 나쁜 것인지 없는 것인지 알 수 없다.
    vi.spyOn(api, 'hitList').mockResolvedValue(
      list({ rows: [], total_rows: 0, empty_reason: 'tier 별 후보를 남기지 않았습니다.' }) as never,
    )

    render(<HitList runId="run-0001" />)

    expect(await screen.findByText(/tier 별 후보를 남기지 않았습니다/)).toBeInTheDocument()
  })

  it('가중치를 바꿔 적용하면 그 값으로 다시 묻는다', async () => {
    const call = vi.spyOn(api, 'hitList').mockResolvedValue(list() as never)
    render(<HitList runId="run-0001" />)
    await screen.findByText('0.813')

    fireEvent.change(screen.getByLabelText('용해도'), { target: { value: '0.9' } })
    fireEvent.click(screen.getByRole('button', { name: '가중치 적용' }))

    await waitFor(() =>
      expect(call).toHaveBeenLastCalledWith(
        'run-0001',
        expect.objectContaining({ soluprot: 0.9 }),
      ),
    )
  })

  it('바꾸기 전에는 적용 단추가 눌리지 않는다', async () => {
    vi.spyOn(api, 'hitList').mockResolvedValue(list() as never)

    render(<HitList runId="run-0001" />)

    expect(await screen.findByRole('button', { name: '가중치 적용' })).toBeDisabled()
  })

  it('통과 여부를 색이 아니라 표시로 가른다', async () => {
    //  화면의 색은 실행 상태 5종뿐이고, 이 표는 흑백으로 인쇄된다.
    vi.spyOn(api, 'hitList').mockResolvedValue(list() as never)

    render(<HitList runId="run-0001" />)

    expect(await screen.findByLabelText('용해도 통과')).toBeInTheDocument()
    expect(screen.getByLabelText('구조 예측 선정 미통과')).toBeInTheDocument()
  })

  it('조회에 실패하면 사유를 보여준다', async () => {
    vi.spyOn(api, 'hitList').mockRejectedValue(new Error('실행을 찾지 못했습니다'))

    render(<HitList runId="run-0001" />)

    expect(await screen.findByText('실행을 찾지 못했습니다')).toBeInTheDocument()
  })
})
