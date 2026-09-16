import type { NodeKind } from '../../api/client'

/**
 * What is missing from a workflow before it is worth saving.
 *
 * The canvas already shows what each node *is* - the label carries the model
 * id, the condition, the branch marking. What it does not do is tell you what
 * is *absent*, because absence has no shape: at a zoom that fits ten nodes,
 * finding the one that says "모델 미지정" means reading all ten.
 *
 * So this is not a second view of the graph. It is the short list of things
 * that would fail, or silently do nothing, at run time - and it is empty far
 * more often than not, which is why the panel that shows it disappears when
 * there is nothing in it.
 *
 * Some of these the server refuses outright (see build_graph in engine/dag.py)
 * and some it accepts. Both belong here: the point is to say so before 「저장」
 * rather than after, and a rejected save says which node only in prose.
 */

/** Which part of the editor panel fixes this. */
export type IssueField = 'model' | 'condition' | 'edges' | 'links'

export interface Issue {
  /** Stable across re-renders, so React keeps the row. */
  key: string
  node_id: string
  field: IssueField
  text: string
}

export interface GraphShape {
  nodes: { id: string }[]
  edges: { id: string; source: string; target: string; branch: 'true' | 'false' | null }[]
  kinds: Record<string, NodeKind>
  models: Record<string, string>
  conditions: Record<string, string>
}

export function inspect(g: GraphShape): Issue[] {
  const issues: Issue[] = []
  const linked = new Set<string>()
  for (const e of g.edges) {
    linked.add(e.source)
    linked.add(e.target)
  }

  const outgoing = new Map<string, number>()
  for (const e of g.edges) outgoing.set(e.source, (outgoing.get(e.source) ?? 0) + 1)

  for (const n of g.nodes) {
    const kind = g.kinds[n.id] ?? 'model'

    //  A model node with no model runs nothing. The engine would reject it,
    //  but only once the run had already started and cost something.
    if (kind === 'model' && !g.models[n.id]) {
      issues.push({ key: `${n.id}:model`, node_id: n.id, field: 'model', text: '모델 미지정' })
    }

    //  An empty condition is worse than a wrong one: the branch takes the same
    //  path every time and the run looks like it worked.
    if (kind === 'branch' && !(g.conditions[n.id] ?? '').trim()) {
      issues.push({
        key: `${n.id}:condition`,
        node_id: n.id,
        field: 'condition',
        text: '조건식 비어 있음',
      })
    }

    //  A branch that goes nowhere is refused on save, and there is no reading
    //  of it that could work: the condition would decide between nothing.
    if (kind === 'branch' && !outgoing.get(n.id)) {
      issues.push({
        key: `${n.id}:outgoing`,
        node_id: n.id,
        field: 'edges',
        text: '나가는 간선이 없음',
      })
    }

    //  One node on its own is a canvas someone has not finished, not a defect.
    if (g.nodes.length > 1 && !linked.has(n.id)) {
      issues.push({
        key: `${n.id}:links`,
        node_id: n.id,
        field: 'links',
        text: '어디에도 연결되지 않음',
      })
    }
  }

  for (const e of g.edges) {
    //  Easy to draw by accident - the two handles of one node are close
    //  together - and refused on save.
    if (e.source === e.target) {
      issues.push({
        key: `${e.id}:self`,
        node_id: e.source,
        field: 'edges',
        text: '자기 자신으로 가는 간선',
      })
      continue
    }

    //  An unmarked edge out of a branch is taken whichever way the condition
    //  went, which defeats the branch.
    if (g.kinds[e.source] !== 'branch' || e.branch) continue
    issues.push({
      key: `${e.id}:branch`,
      node_id: e.source,
      field: 'edges',
      text: `${e.target} 로 가는 간선의 참/거짓 미지정`,
    })
  }

  //  Follow the graph's own order, so the list reads the way the canvas does.
  const order = new Map(g.nodes.map((n, i) => [n.id, i]))
  return issues.sort((a, b) => (order.get(a.node_id) ?? 0) - (order.get(b.node_id) ?? 0))
}
