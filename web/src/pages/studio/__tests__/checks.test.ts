/**
 * What the check panel reports.
 *
 * Every rule here describes something that costs GPU time to discover at run
 * time, so the cases that matter most are the ones that must *not* be
 * reported: a panel that cries wolf is one people stop reading.
 */

import { describe, expect, it } from 'vitest'

import { inspect, type GraphShape } from '../checks'

function graph(over: Partial<GraphShape> = {}): GraphShape {
  return {
    nodes: [{ id: 'a' }, { id: 'b' }],
    edges: [{ id: 'e1', source: 'a', target: 'b', branch: null }],
    kinds: { a: 'model', b: 'model' },
    models: { a: 'msa', b: 'rfd3' },
    conditions: {},
    ...over,
  }
}

const texts = (g: GraphShape) => inspect(g).map((i) => i.text)

describe('워크플로 점검', () => {
  it('온전한 그래프에는 아무것도 내지 않는다', () => {
    expect(inspect(graph())).toEqual([])
  })

  it('모델을 지정하지 않은 모델 노드를 짚는다', () => {
    const found = inspect(graph({ models: { a: '', b: 'rfd3' } }))

    expect(found).toHaveLength(1)
    expect(found[0]).toMatchObject({ node_id: 'a', field: 'model' })
  })

  it('모델 노드가 아니면 모델이 없어도 짚지 않는다', () => {
    //  A join has no model by definition. Reporting it would mean the panel
    //  was never empty.
    expect(
      inspect(graph({ kinds: { a: 'model', b: 'join' }, models: { a: 'msa', b: '' } })),
    ).toEqual([])
  })

  it('비어 있는 조건식을 짚는다', () => {
    const g = graph({ kinds: { a: 'branch', b: 'model' }, models: { b: 'rfd3' } })
    expect(texts(g)).toContain('조건식 비어 있음')
  })

  it('공백만 있는 조건식도 비어 있는 것으로 본다', () => {
    const g = graph({
      kinds: { a: 'branch', b: 'model' },
      models: { b: 'rfd3' },
      conditions: { a: '   ' },
    })
    expect(texts(g)).toContain('조건식 비어 있음')
  })

  it('분기에서 나가는 간선의 참·거짓 미지정을 짚는다', () => {
    const g = graph({
      kinds: { a: 'branch', b: 'model' },
      models: { b: 'rfd3' },
      conditions: { a: 'soluprot.pass_rate > 0.3' },
    })

    const found = inspect(g)
    expect(found).toHaveLength(1)
    expect(found[0]).toMatchObject({ node_id: 'a', field: 'edges' })
  })

  it('분기가 아닌 노드에서 나가는 간선은 표시가 없어도 된다', () => {
    expect(inspect(graph())).toEqual([])
  })

  it('어디에도 연결되지 않은 노드를 짚는다', () => {
    const g = graph({
      nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      kinds: { a: 'model', b: 'model', c: 'model' },
      models: { a: 'msa', b: 'rfd3', c: 'af2' },
    })

    expect(inspect(g)).toHaveLength(1)
    expect(inspect(g)[0]).toMatchObject({ node_id: 'c', field: 'links' })
  })

  it('노드가 하나뿐이면 연결을 따지지 않는다', () => {
    //  One node on a canvas is someone who has just started, not a defect.
    const g = graph({
      nodes: [{ id: 'a' }],
      edges: [],
      kinds: { a: 'model' },
      models: { a: 'msa' },
    })

    expect(inspect(g)).toEqual([])
  })

  it('캔버스와 같은 순서로 낸다', () => {
    const g = graph({
      nodes: [{ id: 'z' }, { id: 'a' }],
      edges: [{ id: 'e1', source: 'z', target: 'a', branch: null }],
      kinds: { z: 'model', a: 'model' },
      models: { z: '', a: '' },
    })

    expect(inspect(g).map((i) => i.node_id)).toEqual(['z', 'a'])
  })

  it('한 노드에 문제가 둘이면 둘 다 낸다', () => {
    const g = graph({
      nodes: [{ id: 'a' }, { id: 'b' }],
      edges: [],
      kinds: { a: 'model', b: 'model' },
      models: { a: '', b: 'rfd3' },
    })

    const found = inspect(g).filter((i) => i.node_id === 'a')
    expect(found.map((i) => i.field).sort()).toEqual(['links', 'model'])
  })

  it('항목마다 키가 겹치지 않는다', () => {
    const g = graph({
      nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      edges: [
        { id: 'e1', source: 'a', target: 'b', branch: null },
        { id: 'e2', source: 'a', target: 'c', branch: null },
      ],
      kinds: { a: 'branch', b: 'model', c: 'model' },
      models: { b: '', c: '' },
      conditions: {},
    })

    const keys = inspect(g).map((i) => i.key)
    expect(new Set(keys).size).toBe(keys.length)
  })
})

describe('저장이 거부될 조건', () => {
  //  These mirror build_graph in engine/dag.py. Catching them here turns a
  //  rejected save into a note beside the node it is about.

  it('나가는 간선이 없는 조건 분기를 짚는다', () => {
    const g = graph({
      nodes: [{ id: 'a' }, { id: 'gate' }],
      edges: [{ id: 'e1', source: 'a', target: 'gate', branch: null }],
      kinds: { a: 'model', gate: 'branch' },
      models: { a: 'msa' },
      conditions: { gate: 'soluprot.pass_rate > 0.3' },
    })

    expect(texts(g)).toContain('나가는 간선이 없음')
  })

  it('자기 자신으로 가는 간선을 짚는다', () => {
    const g = graph({
      edges: [
        { id: 'e1', source: 'a', target: 'b', branch: null },
        { id: 'e2', source: 'b', target: 'b', branch: null },
      ],
    })

    expect(texts(g)).toEqual(['자기 자신으로 가는 간선'])
  })

  it('자기 자신으로 가는 간선을 분기 미표시로도 세지 않는다', () => {
    //  One edge, one complaint: naming it twice would read as two defects.
    const g = graph({
      nodes: [{ id: 'gate' }, { id: 'b' }],
      edges: [
        { id: 'e1', source: 'gate', target: 'gate', branch: null },
        { id: 'e2', source: 'gate', target: 'b', branch: 'true' },
      ],
      kinds: { gate: 'branch', b: 'model' },
      models: { b: 'af2' },
      conditions: { gate: 'soluprot.pass_rate > 0.3' },
    })

    expect(texts(g)).toEqual(['자기 자신으로 가는 간선'])
  })
})
