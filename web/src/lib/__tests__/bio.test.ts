/**
 * The summary line under an input field.
 *
 * It only has to be right about what it says - record count, lengths,
 * chains - and it has to agree with the server about what is a path and
 * what is content, or the field would describe a value one way and the run
 * would read it another.
 */

import { describe, expect, it } from 'vitest'

import { formatResidues, looksLikePath, parseFasta, summarizePdb } from '../bio'

describe('FASTA 요약', () => {
  it('기록 수와 길이를 센다', () => {
    const s = parseFasta('>a\nMKAL\nIVLG\n>b desc\nQW\n')
    expect(s.ok).toBe(true)
    expect(s.records).toEqual([
      { id: 'a', length: 8 },
      { id: 'b', length: 2 },
    ])
  })

  it('헤더가 없으면 FASTA 가 아니다', () => {
    expect(parseFasta('MKALIVLG').ok).toBe(false)
  })

  it('줄 안의 공백과 숫자는 잔기로 세지 않는다', () => {
    //  GenBank-style numbered lines and spaced blocks are common in pastes.
    expect(parseFasta('>x\n1 MKAL IVLG\n11 QW\n').records[0].length).toBe(10)
  })

  it('윈도우 줄바꿈도 읽는다', () => {
    expect(parseFasta('>x\r\nMK\r\nAL\r\n').records[0].length).toBe(4)
  })

  it('이름 없는 헤더에는 차례 번호를 준다', () => {
    expect(parseFasta('>\nMK\n').records[0].id).toBe('seq1')
  })
})

describe('PDB 요약', () => {
  const atom = (chain: string, i: number) =>
    `ATOM  ${String(i).padStart(5)}  CA  ALA ${chain}${String(i).padStart(4)}      0.000   0.000   0.000  1.00  0.00           C`

  it('체인과 원자 수를 센다', () => {
    const s = summarizePdb([atom('A', 1), atom('A', 2), atom('B', 3)].join('\n'))
    expect(s.ok).toBe(true)
    expect(s.chains).toEqual(['A', 'B'])
    expect(s.atoms).toBe(3)
  })

  it('HETATM 은 원자로 세되 체인으로는 세지 않는다', () => {
    //  Waters and ligands sit on chains of their own; a design chain list
    //  that included them would be wrong.
    const het = 'HETATM 1000  O   HOH W 500       0.000   0.000   0.000  1.00  0.00           O'
    const s = summarizePdb([atom('A', 1), het].join('\n'))
    expect(s.chains).toEqual(['A'])
    expect(s.atoms).toBe(2)
  })

  it('ATOM 기록이 없으면 PDB 가 아니다', () => {
    expect(summarizePdb('HEADER    HYDROLASE\nEND\n').ok).toBe(false)
  })

  it('체인을 정렬해 낸다', () => {
    expect(summarizePdb([atom('C', 1), atom('A', 2)].join('\n')).chains).toEqual(['A', 'C'])
  })
})

describe('경로인가 내용인가', () => {
  //  Must match StageInput.text in engine/payloads.py.
  it('줄바꿈 없고 > 로 시작하지 않으면 경로다', () => {
    expect(looksLikePath('/data/targets/lys.fasta')).toBe(true)
  })

  it('> 로 시작하면 내용이다', () => {
    expect(looksLikePath('>lys')).toBe(false)
  })

  it('줄바꿈이 있으면 내용이다', () => {
    expect(looksLikePath('ATOM 1\nATOM 2')).toBe(false)
  })

  it('빈 값은 경로가 아니다', () => {
    expect(looksLikePath('   ')).toBe(false)
  })
})

describe('잔기 수 표기', () => {
  const r = (...lengths: number[]) => lengths.map((length, i) => ({ id: String(i), length }))

  it('넷까지는 전부 적는다', () => {
    expect(formatResidues(r(147, 150))).toBe('147, 150 잔기')
  })

  it('다섯부터는 범위로 줄인다', () => {
    expect(formatResidues(r(10, 20, 30, 40, 50))).toBe('10–50 잔기')
  })

  it('전부 같은 길이면 하나로 적는다', () => {
    expect(formatResidues(r(9, 9, 9, 9, 9))).toBe('9 잔기')
  })
})
