/**
 * 외부 GPU 운영 화면.
 *
 * 개발 기계에는 RunPod 자격 증명이 없다. 그래서 이 화면이 실제로 하는 일의
 * 대부분은 **왜 비어 있는지 말하는 것**이고, 시험도 거기에 집중한다.
 * 빈 표는 「설정 안 함」과 「닿지 못함」과 「없음」을 구분해 주지 않는다.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { IdentityProvider } from '@/lib/identity'
import { Gpu } from '../Gpu'

function show(roles: string[] = ['admin']) {
  vi.spyOn(api, 'me').mockResolvedValue({
    user_id: 'dev', email: '', roles, authenticated: false, auth_mode: 'disabled',
  } as never)
  render(
    <MemoryRouter>
      <IdentityProvider>
        <Gpu />
      </IdentityProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('외부 GPU 운영', () => {
  it('자격 증명이 없으면 그렇게 말하고 목록을 부르지 않는다', async () => {
    vi.spyOn(api, 'gpuStatus').mockResolvedValue({
      configured: false, reachable: false,
      reason: 'RUNPOD_API_KEY 가 없습니다.',
    } as never)
    const list = vi.spyOn(api, 'gpuEndpoints')

    show()

    expect(await screen.findByText(/RUNPOD_API_KEY 가 없습니다/)).toBeInTheDocument()
    expect(screen.getByText('없음')).toBeInTheDocument()
    expect(list).not.toHaveBeenCalled()
  })

  it('자격 증명은 있는데 닿지 못하면 그 둘을 구분해 말한다', async () => {
    vi.spyOn(api, 'gpuStatus').mockResolvedValue({
      configured: true, reachable: false, reason: '키가 거절되었습니다',
    } as never)

    show()

    expect(await screen.findByText(/자격 증명은 있으나 닿지 못했습니다/)).toBeInTheDocument()
    expect(screen.getByText('설정됨')).toBeInTheDocument()
    expect(screen.getByText('실패')).toBeInTheDocument()
  })

  it('연결되면 엔드포인트와 워커를 낸다', async () => {
    vi.spyOn(api, 'gpuStatus').mockResolvedValue({
      configured: true, reachable: true, endpoints: 1,
    } as never)
    vi.spyOn(api, 'gpuEndpoints').mockResolvedValue({
      endpoints: [{
        id: 'ep-proteinmpnn', name: 'ProteinMPNN', managed: true,
        workersMin: 0, workersMax: 3, gpuTypeIds: ['NVIDIA RTX A5000'],
        health: { jobs: { inQueue: 2, inProgress: 1 } },
      }],
    } as never)
    vi.spyOn(api, 'gpuBilling').mockResolvedValue({ totalCost: 12.5 } as never)

    show()

    expect(await screen.findByText('ep-proteinmpnn')).toBeInTheDocument()
    expect(screen.getByText('0 / 3')).toBeInTheDocument()
    expect(screen.getByText('쓰는 중')).toBeInTheDocument()
    expect(screen.getByText('NVIDIA RTX A5000')).toBeInTheDocument()
  })

  it('연구자에게는 비용과 워커 조정을 내지 않는다', async () => {
    vi.spyOn(api, 'gpuStatus').mockResolvedValue({
      configured: true, reachable: true, endpoints: 1,
    } as never)
    vi.spyOn(api, 'gpuEndpoints').mockResolvedValue({
      endpoints: [{ id: 'ep-1', managed: true, workersMax: 2 }],
    } as never)
    const bill = vi.spyOn(api, 'gpuBilling')

    show(['researcher'])

    await screen.findByText('ep-1')
    expect(bill).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '워커 조정' })).not.toBeInTheDocument()
  })

  it('운영자에게는 비용을 낸다', async () => {
    vi.spyOn(api, 'gpuStatus').mockResolvedValue({
      configured: true, reachable: true, endpoints: 0,
    } as never)
    vi.spyOn(api, 'gpuEndpoints').mockResolvedValue({ endpoints: [] } as never)
    vi.spyOn(api, 'gpuBilling').mockResolvedValue({ totalCost: 12.5 } as never)

    show()

    expect(await screen.findByText('totalCost')).toBeInTheDocument()
    expect(screen.getByText('12.5')).toBeInTheDocument()
  })
})
