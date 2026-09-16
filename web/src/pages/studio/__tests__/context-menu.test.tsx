/**
 * The canvas context menu.
 *
 * Its behaviour is about reach and dismissal, neither of which needs a canvas:
 * that it shows what it was given, acts once, and closes when it should.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ContextMenu, type MenuState } from '../ContextMenu'

const at: MenuState = { kind: 'pane', x: 40, y: 60 }

function open(items = [{ label: '모델', onSelect: vi.fn() }], onClose = vi.fn()) {
  render(<ContextMenu state={at} title="노드 추가" items={items} onClose={onClose} />)
  return { items, onClose }
}

describe('컨텍스트 메뉴', () => {
  it('제목과 항목을 낸다', () => {
    open()
    expect(screen.getByRole('menu', { name: '노드 추가' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: '모델' })).toBeInTheDocument()
  })

  it('고르면 한 번 실행하고 닫는다', () => {
    const { items, onClose } = open()
    fireEvent.click(screen.getByRole('menuitem', { name: '모델' }))

    expect(items[0].onSelect).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalled()
  })

  it('Escape 로 닫는다', () => {
    const { onClose } = open()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('바깥을 누르면 닫는다', () => {
    const { onClose } = open()
    fireEvent.pointerDown(document.body)
    expect(onClose).toHaveBeenCalled()
  })

  it('메뉴 안을 눌러도 닫히지 않는다', () => {
    const onClose = vi.fn()
    open([{ label: '모델', onSelect: vi.fn() }], onClose)

    fireEvent.pointerDown(screen.getByRole('menu'))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('캔버스가 움직이면 닫는다', () => {
    const { onClose } = open()
    fireEvent.scroll(window)
    expect(onClose).toHaveBeenCalled()
  })

  it('현재 값인 항목을 표시한다', () => {
    render(
      <ContextMenu
        state={{ ...at, kind: 'edge', id: 'e1' }}
        title="a → b"
        items={[
          { label: '참일 때', onSelect: vi.fn(), active: true },
          { label: '거짓일 때', onSelect: vi.fn() },
        ]}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole('menuitem', { name: /참일 때/ })).toHaveTextContent('현재')
    expect(screen.getByRole('menuitem', { name: /거짓일 때/ })).not.toHaveTextContent('현재')
  })
})
