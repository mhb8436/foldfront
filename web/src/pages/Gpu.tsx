import { useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { api, type GpuEndpoint } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { useIdentity } from '@/lib/identity'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel, Stat, StatusDot } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/**
 * External GPU operations.
 *
 * The models run on RunPod serverless endpoints and the original already
 * had the admin surface for them. This screen calls it.
 *
 * Everything here needs credentials, so the screen asks whether they exist
 * before it asks anything else. Three states have to be told apart and an
 * empty table tells none of them: no key configured, a key the provider
 * will not take, and a working connection with nothing on it. An operator
 * looking at an empty list needs to know which one they are in.
 *
 * Cost is only fetched for an operator. It is not a secret, but it is not
 * every reader's business either, and the server refuses it anyway.
 */
export function Gpu() {
  const { is } = useIdentity()
  const admin = is('admin')
  const status = useAsync(() => api.gpuStatus(), [])
  const live = status.data?.configured && status.data?.reachable

  return (
    <>
      <PageHeader
        title="외부 GPU 운영"
        description="모델이 도는 엔드포인트의 상태 · 워커 · 사용량을 봅니다."
        actions={
          <Button variant="outline" size="sm" onClick={() => status.reload()}>
            <RefreshCw />
            새로고침
          </Button>
        }
      />
      <ErrorBox message={status.error} />

      {status.data && !status.data.configured && (
        <Notice
          message={
            status.data.reason ??
            'RUNPOD_API_KEY 가 없습니다. 외부 GPU 관제는 자격 증명이 있어야 합니다.'
          }
        />
      )}
      {status.data?.configured && !status.data.reachable && (
        <ErrorBox
          message={`자격 증명은 있으나 닿지 못했습니다 — ${status.data.reason ?? '사유 없음'}`}
        />
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label="자격 증명"
          value={status.data?.configured ? '설정됨' : '없음'}
          caption="RUNPOD_API_KEY"
        />
        <Stat
          label="연결"
          value={live ? '정상' : status.data?.configured ? '실패' : '해당 없음'}
          caption="관제 API 응답"
        />
        <Stat
          label="엔드포인트"
          value={status.data?.endpoints ?? 0}
          caption="보이는 것"
        />
      </div>

      {live ? (
        <>
          <Endpoints admin={admin} />
          {admin && <Billing />}
        </>
      ) : (
        <Panel title="엔드포인트">
          <Empty>
            {status.loading
              ? '확인 중입니다.'
              : status.data?.configured
                ? '관제 API 에 닿지 못해 목록을 읽을 수 없습니다.'
                : '자격 증명을 설정하면 여기에 엔드포인트와 워커가 나옵니다. 지금까지의 실행은 모의 어댑터이거나 자체 호스팅 워커입니다.'}
          </Empty>
        </Panel>
      )}
    </>
  )
}

function num(value: unknown): string {
  return typeof value === 'number' ? String(value) : '—'
}

function Endpoints({ admin }: { admin: boolean }) {
  const list = useAsync(() => api.gpuEndpoints({ include_workers: true }), [])
  const [editing, setEditing] = useState<string | null>(null)
  const [maxWorkers, setMaxWorkers] = useState('')
  const [note, setNote] = useState<string | null>(null)

  const items = list.data?.endpoints ?? []

  async function save(id: string) {
    setNote(null)
    try {
      await api.gpuUpdateEndpoint(id, { workersMax: Number(maxWorkers) })
      setEditing(null)
      setNote(`${id} 의 최대 워커를 ${maxWorkers} 로 바꿨습니다.`)
      list.reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  return (
    <Panel
      title="엔드포인트"
      description={`${items.length}개`}
      bodyClassName={items.length ? 'p-0' : undefined}
    >
      <ErrorBox message={list.error} />
      {note && <Notice message={note} />}
      {items.length === 0 ? (
        <Empty>{list.loading ? '불러오는 중입니다.' : '보이는 엔드포인트가 없습니다.'}</Empty>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[200px]">엔드포인트</TableHead>
              <TableHead className="w-[90px]">사용</TableHead>
              <TableHead className="w-[150px]">GPU</TableHead>
              <TableHead className="w-[110px]">워커 최소/최대</TableHead>
              <TableHead className="w-[100px]">대기</TableHead>
              <TableHead>진행 중</TableHead>
              {admin && <TableHead className="w-[170px]" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((e: GpuEndpoint) => {
              const id = String(e.id ?? e.name ?? '—')
              const health = (e.health ?? {}) as Record<string, unknown>
              const jobs = (health.jobs ?? {}) as Record<string, unknown>
              return (
                <TableRow key={id}>
                  <TableCell>
                    <div className="font-mono text-[12.5px]">{id}</div>
                    {e.name && e.name !== id && (
                      <div className="text-muted-foreground text-[11.5px]">{e.name}</div>
                    )}
                  </TableCell>
                  <TableCell>
                    {/*  Whether this install actually routes to it. An
                         account can hold endpoints nothing here uses. */}
                    <span className="inline-flex items-center gap-1.5 text-[12.5px]">
                      <StatusDot status={e.managed ? 'succeeded' : 'cancelled'} />
                      {e.managed ? '쓰는 중' : '안 씀'}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[12px]">
                    {(e.gpuTypeIds ?? []).join(', ') || '—'}
                  </TableCell>
                  <TableCell className="tabular font-mono text-[12.5px]">
                    {num(e.workersMin)} / {num(e.workersMax)}
                  </TableCell>
                  <TableCell className="tabular font-mono text-[12.5px]">
                    {num(jobs.inQueue)}
                  </TableCell>
                  <TableCell className="tabular font-mono text-[12.5px]">
                    {num(jobs.inProgress)}
                  </TableCell>
                  {admin && (
                    <TableCell>
                      {editing === id ? (
                        <div className="flex items-center gap-1.5">
                          <Input
                            value={maxWorkers}
                            onChange={(ev) => setMaxWorkers(ev.target.value)}
                            type="number"
                            min={0}
                            aria-label={`${id} 최대 워커`}
                            className="h-8 w-20 font-mono text-[12.5px]"
                          />
                          <Button size="sm" onClick={() => save(id)}>
                            저장
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
                            취소
                          </Button>
                        </div>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          title="워커를 늘리면 비용이 늘고, 줄이면 실행이 큐에서 기다립니다"
                          onClick={() => {
                            setEditing(id)
                            setMaxWorkers(String(e.workersMax ?? 0))
                          }}
                        >
                          워커 조정
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}

function Billing() {
  const [days, setDays] = useState(30)
  const bill = useAsync(() => api.gpuBilling(days), [days])

  //  The provider's shape is its own and changes; rendering whatever came
  //  back beats a table of columns that quietly go blank after an update.
  const rows = Object.entries(bill.data ?? {}).filter(
    ([, v]) => v !== null && typeof v !== 'object',
  )

  return (
    <Panel
      title="사용량과 비용"
      description={`최근 ${days}일`}
      actions={
        <div className="flex gap-1.5">
          {[7, 30, 90].map((d) => (
            <Button
              key={d}
              variant={d === days ? 'default' : 'outline'}
              size="sm"
              onClick={() => setDays(d)}
            >
              {d}일
            </Button>
          ))}
        </div>
      }
    >
      <ErrorBox message={bill.error} />
      {rows.length === 0 ? (
        <Empty>{bill.loading ? '불러오는 중입니다.' : '집계가 없습니다.'}</Empty>
      ) : (
        <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-4 border-b py-1">
              <dt className="text-muted-foreground text-[12.5px]">{k}</dt>
              <dd className="tabular font-mono text-[12.5px]">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  )
}
