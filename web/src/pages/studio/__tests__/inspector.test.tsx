/**
 * The node editor that floats over the canvas.
 *
 * What it owes the canvas is that it shows the right controls for the kind of
 * node it was opened on - six kinds share one panel, and offering a model
 * picker on a join would be worse than offering nothing.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Inspector, type InspectorEdge } from '../Inspector'
import type { NodeKind } from '../../../api/client'

const edge = (over: Partial<InspectorEdge> = {}): InspectorEdge => ({
  id: 'e1',
  source: 'branch-1',
  target: 'af2',
  branch: null,
  ...over,
})

function show(over: Partial<Parameters<typeof Inspector>[0]> = {}) {
  const props = {
    nodeId: 'model-1',
    kind: 'model' as NodeKind,
    modelIds: ['msa', 'rfd3', 'soluprot'],
    model: '',
    onModel: vi.fn(),
    condition: '',
    onCondition: vi.fn(),
    instructions: '',
    onInstructions: vi.fn(),
    outgoing: [] as InspectorEdge[],
    incoming: [] as InspectorEdge[],
    onEdgeBranch: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
    readOnly: false,
    ...over,
  }
  render(<Inspector {...props} />)
  return props
}

describe('노드 편집 패널', () => {
  it('어느 노드인지 식별자로 밝힌다', () => {
    show()
    expect(screen.getByRole('complementary', { name: 'model-1 편집' })).toBeInTheDocument()
    expect(screen.getByText('model-1')).toBeInTheDocument()
  })

  it('들어오고 나가는 간선 수를 센다', () => {
    show({ incoming: [edge({ id: 'i1' })], outgoing: [edge({ id: 'o1' }), edge({ id: 'o2' })] })
    expect(screen.getByText(/들어오는 1 · 나가는 2/)).toBeInTheDocument()
  })

  it('모델 노드에는 모델 선택을 낸다', () => {
    const props = show()
    fireEvent.change(screen.getByLabelText('실행할 모델'), { target: { value: 'rfd3' } })
    expect(props.onModel).toHaveBeenCalledWith('rfd3')
  })

  it('조건 분기에는 조건식만 낸다', () => {
    show({ nodeId: 'branch-1', kind: 'branch' })

    expect(screen.getByLabelText('조건식')).toBeInTheDocument()
    expect(screen.queryByLabelText('실행할 모델')).not.toBeInTheDocument()
  })

  it('조건식을 고치면 그대로 넘긴다', () => {
    const props = show({ nodeId: 'branch-1', kind: 'branch' })
    fireEvent.change(screen.getByLabelText('조건식'), {
      target: { value: 'soluprot.pass_rate > 0.3' },
    })
    expect(props.onCondition).toHaveBeenCalledWith('soluprot.pass_rate > 0.3')
  })

  it('분기에서 나가는 간선에만 참·거짓을 묻는다', () => {
    const props = show({ nodeId: 'branch-1', kind: 'branch', outgoing: [edge()] })

    fireEvent.change(screen.getByLabelText('af2 로 가는 간선의 가지'), {
      target: { value: 'true' },
    })
    expect(props.onEdgeBranch).toHaveBeenCalledWith('e1', 'true')
  })

  it('분기가 아니면 나가는 간선을 순차로 적는다', () => {
    show({ outgoing: [edge({ source: 'model-1' })] })

    expect(screen.queryByLabelText(/가는 간선의 가지/)).not.toBeInTheDocument()
    expect(screen.getByText('순차')).toBeInTheDocument()
  })

  it('합류 노드에는 편집할 항목이 없다', () => {
    show({ nodeId: 'join-1', kind: 'join' })

    expect(screen.queryByLabelText('실행할 모델')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('조건식')).not.toBeInTheDocument()
  })

  it('닫기를 누르면 닫는다', () => {
    const props = show()
    fireEvent.click(screen.getByRole('button', { name: '닫기' }))
    expect(props.onClose).toHaveBeenCalled()
  })

  it('복제와 삭제를 전한다', () => {
    const props = show()
    fireEvent.click(screen.getByRole('button', { name: /복제/ }))
    fireEvent.click(screen.getByRole('button', { name: /삭제/ }))

    expect(props.onDuplicate).toHaveBeenCalled()
    expect(props.onDelete).toHaveBeenCalled()
  })

  it('쓰기 권한이 없으면 고칠 수 없되 볼 수는 있다', () => {
    show({ model: 'msa', readOnly: true })

    expect(screen.getByLabelText('실행할 모델')).toBeDisabled()
    expect(screen.getByRole('button', { name: /삭제/ })).toBeDisabled()
    //  Closing is not a write, so it stays available.
    expect(screen.getByRole('button', { name: '닫기' })).not.toBeDisabled()
  })

  it('고칠 항목을 지정해 열면 그 칸에 초점을 준다', () => {
    show({ nodeId: 'branch-1', kind: 'branch', focus: 'condition' })
    expect(screen.getByLabelText('조건식')).toHaveFocus()
  })

  it('검토 지점은 지시문을 편집하게 한다', () => {
    const props = show({ kind: 'checkpoint' as NodeKind })

    fireEvent.change(screen.getByLabelText('검토 지시문'), {
      target: { value: '정렬 깊이를 확인하십시오' },
    })

    expect(props.onInstructions).toHaveBeenCalledWith('정렬 깊이를 확인하십시오')
  })

  it('검토 지점에는 모델 선택이 없다', () => {
    show({ kind: 'checkpoint' as NodeKind })

    expect(screen.queryByLabelText('실행할 모델')).not.toBeInTheDocument()
  })
})
