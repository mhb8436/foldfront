import { describe, expect, it } from 'vitest'

import { count, toggle, type FixedPositions } from '../ResiduePicker'

describe('residue toggle', () => {
  it('adds a residue under its chain', () => {
    expect(toggle({}, 'A', 6)).toEqual({ A: [6] })
  })

  it('keeps each chain sorted regardless of click order', () => {
    let v: FixedPositions = {}
    v = toggle(v, 'A', 10)
    v = toggle(v, 'A', 6)
    expect(v).toEqual({ A: [6, 10] })
  })

  it('clicking a held residue again releases it', () => {
    const v = toggle({ A: [6, 10] }, 'A', 6)
    expect(v).toEqual({ A: [10] })
  })

  it('drops a chain once its last residue is released', () => {
    const v = toggle({ A: [6], B: [2] }, 'A', 6)
    expect(v).toEqual({ B: [2] })
  })

  it('does not mutate the value it was given', () => {
    const before: FixedPositions = { A: [6] }
    toggle(before, 'A', 10)
    expect(before).toEqual({ A: [6] })
  })

  it('counts residues across chains', () => {
    expect(count({ A: [6, 10], B: [2] })).toBe(3)
    expect(count({})).toBe(0)
  })
})
