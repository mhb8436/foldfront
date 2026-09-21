import { useState } from 'react'

import { api, type HitList as HitListData, type HitRow } from '../../api/client'
import { useAsync } from '../../hooks/useAsync'
import { Empty, ErrorBox, Panel } from '../../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/**
 * The ranked candidate list, with the WT difference alongside.
 *
 * Every number in this table is the original's. The weighted score, the
 * per-sequence solubility and pLDDT, the count of residues a design differs
 * from the wild type by - all of it comes back from `pipeline.get_hit_list`
 * and is rendered as it arrived. Nothing is recomputed here, because a second
 * implementation of the ranking would be a second answer to the same
 * question.
 *
 * WT Diff is not a separate screen. The original carries it as columns of
 * this same list - `wt_diff_count`, `wt_diff_pct`, `wt_identity_pct` - which
 * is the right shape: how far a design has moved from the wild type only
 * means something beside how well it scored.
 *
 * The weights are the reader's to set. Which of solubility, fold confidence
 * and novelty should dominate is a question about the design campaign, and
 * the original normalises whatever is given.
 */

const DEFAULTS = { soluprot: 0.4, plddt: 0.3, rmsd: 0.2, novelty: 0.1 }

type Weights = typeof DEFAULTS

/** Fixed width and alignment, so a column of numbers can be read down. */
function Num({ value, digits = 2, suffix }: { value: number | null; digits?: number; suffix?: string }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="text-muted-foreground">—</span>
  }
  return (
    <span className="tabular font-mono">
      {value.toFixed(digits)}
      {suffix}
    </span>
  )
}

/**
 * Whether a candidate cleared a gate, as a mark rather than a colour.
 *
 * The console has five colours and they all mean run status. A filled square
 * against an open one survives being printed in black and white, which is
 * how these tables are actually read.
 */
function Passed({ yes, label }: { yes: boolean; label: string }) {
  return (
    <span
      title={label}
      aria-label={`${label} ${yes ? '통과' : '미통과'}`}
      className={
        yes
          ? 'inline-block size-2.5 rounded-[2px] bg-foreground'
          : 'inline-block size-2.5 rounded-[2px] border border-muted-foreground/50'
      }
    />
  )
}

export function HitList({ runId }: { runId: string }) {
  const [weights, setWeights] = useState<Weights>(DEFAULTS)
  const [applied, setApplied] = useState<Weights>(DEFAULTS)
  const list = useAsync<HitListData>(
    () => api.hitList(runId, { ...applied, limit: 200 }),
    [runId, applied],
  )

  const rows = list.data?.rows ?? []
  const dirty = (Object.keys(DEFAULTS) as Array<keyof Weights>).some(
    (k) => weights[k] !== applied[k],
  )

  return (
    <Panel
      title={`후보군 순위 — ${runId}`}
      description={
        list.data
          ? `전체 ${list.data.total_rows}건 중 ${rows.length}건 표시`
          : undefined
      }
      bodyClassName={rows.length ? 'p-0' : undefined}
      actions={
        <Button
          size="sm"
          variant={dirty ? 'default' : 'outline'}
          disabled={!dirty}
          onClick={() => setApplied(weights)}
        >
          가중치 적용
        </Button>
      }
    >
      <div className="grid grid-cols-2 gap-3 border-b px-4 pb-4 sm:grid-cols-4">
        {(Object.keys(DEFAULTS) as Array<keyof Weights>).map((key) => (
          <div key={key} className="flex flex-col gap-1.5">
            <Label htmlFor={`w-${key}`} className="text-[12px]">
              {LABEL[key]}
            </Label>
            <Input
              id={`w-${key}`}
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={weights[key]}
              onChange={(e) =>
                setWeights((w) => ({ ...w, [key]: Number(e.target.value) }))
              }
              className="h-8 font-mono text-[13px]"
            />
          </div>
        ))}
      </div>

      <ErrorBox message={list.error} />

      {rows.length === 0 ? (
        <Empty>
          {list.data?.empty_reason ??
            (list.loading ? '불러오는 중입니다.' : '순위를 매길 후보가 없습니다.')}
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[54px]">순위</TableHead>
              <TableHead className="w-[210px]">서열 식별자</TableHead>
              <TableHead className="w-[70px]">tier</TableHead>
              <TableHead className="w-[78px]" title="가중 합산 점수">
                점수
              </TableHead>
              <TableHead className="w-[84px]" title="용해도 예측 점수">
                용해도
              </TableHead>
              <TableHead className="w-[78px]" title="구조 예측 신뢰도">
                pLDDT
              </TableHead>
              <TableHead className="w-[78px]" title="기준 구조와의 좌표 차이">
                RMSD
              </TableHead>
              {/*  WT Diff — 야생형과 얼마나 달라졌는가 */}
              <TableHead className="w-[92px]" title="야생형과 다른 잔기 수">
                WT 차이
              </TableHead>
              <TableHead className="w-[86px]" title="야생형과 같은 잔기의 비율">
                WT 동일도
              </TableHead>
              <TableHead className="w-[96px]">통과</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <Row key={`${r.tier}:${r.seq_id}`} row={r} />
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}

const LABEL: Record<keyof Weights, string> = {
  soluprot: '용해도',
  plddt: 'pLDDT',
  rmsd: 'RMSD',
  novelty: 'WT 차이',
}

function Row({ row }: { row: HitRow }) {
  return (
    <TableRow>
      <TableCell className="tabular font-mono text-[12.5px]">{row.rank}</TableCell>
      <TableCell className="truncate font-mono text-[12px]" title={row.seq_id}>
        {row.seq_id}
      </TableCell>
      <TableCell className="tabular font-mono text-[12.5px]">
        {row.tier === null ? '—' : row.tier}
      </TableCell>
      <TableCell className="font-semibold">
        <Num value={row.score} digits={3} />
      </TableCell>
      <TableCell>
        <Num value={row.soluprot} digits={3} />
      </TableCell>
      <TableCell>
        <Num value={row.plddt} digits={1} />
      </TableCell>
      <TableCell>
        <Num value={row.rmsd} digits={2} />
      </TableCell>
      <TableCell>
        {/*  The count is what a bench scientist acts on; the ratio only
             means something next to how long the compared stretch was. */}
        {row.wt_diff_count === null ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <span className="tabular font-mono">
            {row.wt_diff_count}
            {row.wt_compare_len ? (
              <span className="text-muted-foreground">/{row.wt_compare_len}</span>
            ) : null}
          </span>
        )}
      </TableCell>
      <TableCell>
        <Num value={row.wt_identity_pct} digits={1} suffix="%" />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1.5">
          <Passed yes={row.soluprot_passed} label="용해도" />
          <Passed yes={row.af2_candidate} label="구조 예측 대상" />
          <Passed yes={row.af2_selected} label="구조 예측 선정" />
        </div>
      </TableCell>
    </TableRow>
  )
}
