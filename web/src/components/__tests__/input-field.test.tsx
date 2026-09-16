/**
 * The input field for a run.
 *
 * Three ways in - paste, drop, path - and one promise: it says back what it
 * understood before a run is started on it. What is under test is that
 * promise, and that a drop actually leaves the server's path in the value
 * rather than the file's contents.
 */

import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api/client'
import { InputField } from '../InputField'

//  jsdom's File has no text(); every browser's does.
beforeAll(() => {
  if (typeof File.prototype.text !== 'function') {
    File.prototype.text = function () {
      return new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsText(this)
      })
    }
  }
})

/** Holds the value the way Setup does, so what the field says follows what it holds. */
function Harness({
  kind,
  initial = '',
  onChange,
  onSummary,
}: {
  kind: 'fasta' | 'pdb'
  initial?: string
  onChange: (v: string) => void
  onSummary: (s: unknown) => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <InputField
      id="f"
      kind={kind}
      label="대상 서열"
      value={value}
      onChange={(v) => {
        setValue(v)
        onChange(v)
      }}
      onSummary={onSummary}
    />
  )
}

function show(kind: 'fasta' | 'pdb' = 'fasta', initial = '') {
  const onChange = vi.fn()
  const onSummary = vi.fn()
  render(<Harness kind={kind} initial={initial} onChange={onChange} onSummary={onSummary} />)
  return { onChange, onSummary }
}

function pick(file: File) {
  const input = screen.getByLabelText('대상 서열 파일') as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
}

beforeEach(() => vi.restoreAllMocks())

describe('입력 칸', () => {
  it('비어 있으면 어떻게 넣는지 말한다', () => {
    show()
    expect(screen.getByText(/붙여넣거나 파일을 끌어다/)).toBeInTheDocument()
  })

  it('붙여넣은 FASTA 를 요약한다', () => {
    show()
    fireEvent.change(screen.getByLabelText('대상 서열'), {
      target: { value: '>a\nMKAL\n>b\nQWERTY\n' },
    })
    expect(screen.getByText('서열 2개 · 4, 6 잔기')).toBeInTheDocument()
  })

  it('FASTA 가 아니면 그렇다고 말한다', () => {
    show()
    fireEvent.change(screen.getByLabelText('대상 서열'), { target: { value: 'MKALIVLG\nQW' } })
    expect(screen.getByText(/FASTA 로 읽히지 않습니다/)).toBeInTheDocument()
  })

  it('경로를 치면 경로로 읽는다고 말한다', () => {
    show()
    fireEvent.change(screen.getByLabelText('대상 서열'), {
      target: { value: '/data/targets/lys.fasta' },
    })
    expect(screen.getByText('서버에 있는 파일 경로로 읽습니다.')).toBeInTheDocument()
  })

  it('파일을 고르면 올리고 경로를 값으로 남긴다', async () => {
    const upload = vi.spyOn(api, 'uploadInput').mockResolvedValue({
      path: '/srv/data/inputs/20260916/in-abc.fasta',
      name: 'lys.fasta',
      kind: 'fasta',
      size_bytes: 12,
    })
    const { onChange } = show()
    const file = new File(['>lys\nMKALIVLG\n'], 'lys.fasta', { type: 'text/plain' })

    pick(file)

    await waitFor(() => expect(upload).toHaveBeenCalledWith(file))
    //  The value is the server's path, not the file's contents.
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('/srv/data/inputs/20260916/in-abc.fasta'),
    )
    expect(screen.getByText('lys.fasta')).toBeInTheDocument()
    expect(screen.getByText(/서버에 올렸습니다/)).toBeInTheDocument()
    expect(screen.getByText(/서열 1개 · 8 잔기/)).toBeInTheDocument()
  })

  it('올리기가 거절되면 사유를 보이고 값을 바꾸지 않는다', async () => {
    vi.spyOn(api, 'uploadInput').mockRejectedValue(new Error('받지 않는 파일 형식입니다: .sh'))
    const { onChange } = show()

    pick(new File(['x'], 'run.sh', { type: 'text/plain' }))

    await waitFor(() => expect(screen.getByText(/받지 않는 파일 형식/)).toBeInTheDocument())
    expect(onChange).not.toHaveBeenCalled()
  })

  it('PDB 를 요약하면 체인을 알린다', () => {
    const atom = (chain: string) =>
      `ATOM      1  CA  ALA ${chain}   1       0.000   0.000   0.000  1.00  0.00           C`
    const { onSummary } = show('pdb')
    fireEvent.change(screen.getByLabelText('대상 서열'), {
      target: { value: [atom('A'), atom('B')].join('\n') },
    })

    expect(screen.getByText('체인 A, B · 원자 2개')).toBeInTheDocument()
    expect(onSummary).toHaveBeenLastCalledWith(expect.objectContaining({ chains: ['A', 'B'] }))
  })

  it('끌어다 놓아도 올린다', async () => {
    const upload = vi.spyOn(api, 'uploadInput').mockResolvedValue({
      path: '/srv/in.pdb',
      name: 'x.pdb',
      kind: 'pdb',
      size_bytes: 3,
    })
    show('pdb')
    const file = new File(['ATOM'], 'x.pdb', { type: 'text/plain' })

    fireEvent.drop(screen.getByLabelText('대상 서열'), { dataTransfer: { files: [file] } })

    await waitFor(() => expect(upload).toHaveBeenCalledWith(file))
  })
})

describe('검증에서 드러난 것', () => {
  it('헤더 없는 서열은 경로가 아니라 헤더가 없다고 말한다', () => {
    //  The most common paste. By the server's rule it is a path, and the
    //  old hint said so - which pointed the person away from the fix.
    show()
    fireEvent.change(screen.getByLabelText('대상 서열'), {
      target: { value: 'MKALIVLGLVLLSVTVQGKVFERCELARTLKRLG' },
    })
    expect(screen.getByText(/FASTA 헤더가 없습니다/)).toBeInTheDocument()
  })

  it('mmCIF 는 체인을 지어내지 않는다', () => {
    //  Reading column 22 of a whitespace-tokenised mmCIF row yielded a letter
    //  of the residue name, and Setup filled 「체인 G, M」 from it.
    const { onSummary } = show('pdb')
    fireEvent.change(screen.getByLabelText('대상 서열'), {
      target: {
        value: 'data_1ABC\nloop_\n_atom_site.group_PDB\nATOM   1    N  N   . MET A 1 1   ?\nATOM   2    C  CA  . GLY A 1 2   ?',
      },
    })

    expect(screen.getByText(/mmCIF 파일입니다/)).toBeInTheDocument()
    expect(onSummary).toHaveBeenLastCalledWith(expect.objectContaining({ cif: true, chains: [] }))
  })
})
