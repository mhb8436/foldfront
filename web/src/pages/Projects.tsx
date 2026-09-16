import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Plus, RefreshCw } from 'lucide-react'

import { api, type Round } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel, StatusBadge, formatTime } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useIdentity } from '@/lib/identity'
import { useProject, roundLabel } from '@/lib/project'

/**
 * Projects, and the redesign rounds inside them.
 *
 * A round is the unit the original is named for: design, measure solubility,
 * feed the result back, design again. Runs already carried a round, and the
 * engine already linked them, but nothing showed it - so the loop existed in
 * the database and nowhere a person could see.
 *
 * Selecting a project here is the same act as selecting it in the header.
 * There is one selection and one place it is stored.
 */
export function Projects() {
  const { projects, current, select, rounds, loading, reload } = useProject()
  const { canRun } = useIdentity()
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [projectName, setProjectName] = useState('')
  const [roundName, setRoundName] = useState('')
  const [objective, setObjective] = useState('')

  //  Runs of the selected project, so a round can say what came of it.
  const runs = useAsync(
    () =>
      current
        ? api.listRuns({ project_id: current.project_id, limit: 200 })
        : Promise.resolve({ items: [], count: 0 }),
    [current?.project_id],
  )

  function runsOf(round: Round) {
    //  By the run's own round rather than the round's list of run ids: a run
    //  knows which round it belongs to even if the link was never written.
    return (runs.data?.items ?? []).filter((r) => r.round_id === round.round_id)
  }

  async function addProject() {
    setError(null)
    setMessage(null)
    try {
      const p = await api.createProject({ name: projectName.trim() })
      setProjectName('')
      setMessage(`프로젝트를 만들었습니다 — ${p.project_id}`)
      reload()
      select(p.project_id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function addRound() {
    if (!current) return
    setError(null)
    setMessage(null)
    try {
      const r = await api.createRound({
        project_id: current.project_id,
        name: roundName.trim() || null,
        //  Rounds are numbered in the order they were opened, which is what
        //  people mean by "2차".
        index: rounds.length + 1,
        objective: objective.trim() || null,
      })
      setRoundName('')
      setObjective('')
      setMessage(`회차를 열었습니다 — ${roundLabel(r)}`)
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <>
      <PageHeader
        title="프로젝트"
        description="설계 과제와 그 안의 재설계 회차를 관리합니다. 선택한 프로젝트가 다른 화면의 범위가 됩니다."
        actions={
          <Button variant="outline" size="sm" onClick={reload}>
            <RefreshCw />
            새로고침
          </Button>
        }
      />
      <ErrorBox message={error} />
      <Notice message={message} />

      <Panel title="프로젝트" description={`${projects.length}종`} bodyClassName="p-0">
        <div className={projects.length || loading ? '' : 'p-4'}>
        {loading ? (
          <Empty>불러오는 중입니다.</Empty>
        ) : projects.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[220px]">이름</TableHead>
                <TableHead>설명</TableHead>
                <TableHead className="w-[130px]">개설</TableHead>
                <TableHead className="w-[110px]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {projects.map((p) => (
                <TableRow
                  key={p.project_id}
                  data-state={current?.project_id === p.project_id ? 'selected' : undefined}
                >
                  <TableCell>
                    <div>{p.name}</div>
                    <div className="text-muted-foreground font-mono text-[11.5px]">
                      {p.project_id}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-[12.5px]">
                    {p.description ?? '—'}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                    {formatTime(p.created_at ?? null)}
                  </TableCell>
                  <TableCell>
                    {current?.project_id === p.project_id ? (
                      <span className="text-[12px] font-medium">선택됨</span>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => select(p.project_id)}>
                        선택
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty>프로젝트가 없습니다. 아래에서 첫 프로젝트를 개설하십시오.</Empty>
        )}
        </div>

        {canRun && (
          <div className="flex flex-wrap items-end gap-3 border-t p-4">
            <Field className="min-w-[260px] flex-1">
              <Label htmlFor="proj-name">새 프로젝트 이름</Label>
              <Input
                id="proj-name"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="리소자임 용해도 개선"
                onKeyDown={(e) => e.key === 'Enter' && projectName.trim() && addProject()}
              />
            </Field>
            <Button size="sm" onClick={addProject} disabled={!projectName.trim()}>
              <Plus />
              개설
            </Button>
          </div>
        )}
      </Panel>

      {current ? (
        <>
          <Panel
            title="재설계 회차"
            description={`${current.name} · ${rounds.length}회차`}
            bodyClassName="p-0"
            actions={
              <Button variant="outline" size="sm" asChild>
                <Link to="/monitor">
                  실행 감시
                  <ArrowRight />
                </Link>
              </Button>
            }
          >
            {rounds.length ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[180px]">회차</TableHead>
                    <TableHead>목표</TableHead>
                    <TableHead className="w-[220px]">실행</TableHead>
                    <TableHead className="w-[130px]">개설</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rounds.map((r) => {
                    const mine = runsOf(r)
                    return (
                      <TableRow key={r.round_id}>
                        <TableCell>
                          <div>{roundLabel(r)}</div>
                          <div className="text-muted-foreground font-mono text-[11.5px]">
                            {r.round_id}
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground text-[12.5px]">
                          {r.objective ?? '—'}
                        </TableCell>
                        <TableCell>
                          {mine.length ? (
                            <div className="flex flex-wrap gap-1.5">
                              {mine.slice(0, 4).map((run) => (
                                <StatusBadge key={run.run_id} status={run.status} />
                              ))}
                              {mine.length > 4 && (
                                <span className="text-muted-foreground text-[12px]">
                                  외 {mine.length - 4}건
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-muted-foreground text-[12px]">없음</span>
                          )}
                        </TableCell>
                        <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                          {formatTime(r.created_at ?? null)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            ) : (
              <div className="p-4">
                <Empty>회차가 없습니다. 첫 회차를 열고 실행을 붙이십시오.</Empty>
              </div>
            )}

            {canRun && (
              <div className="flex flex-wrap items-end gap-3 border-t p-4">
                <Field className="min-w-[180px] flex-1">
                  <Label htmlFor="round-name">{rounds.length + 1}차 이름</Label>
                  <Input
                    id="round-name"
                    value={roundName}
                    onChange={(e) => setRoundName(e.target.value)}
                    placeholder={`${rounds.length + 1}차 설계`}
                  />
                </Field>
                <Field className="min-w-[260px] flex-[2]">
                  <Label htmlFor="round-objective">목표</Label>
                  <Input
                    id="round-objective"
                    value={objective}
                    onChange={(e) => setObjective(e.target.value)}
                    placeholder="용해도 통과율 0.3 이상"
                    onKeyDown={(e) => e.key === 'Enter' && addRound()}
                  />
                </Field>
                <Button size="sm" onClick={addRound}>
                  <Plus />
                  개설
                </Button>
              </div>
            )}
          </Panel>
        </>
      ) : (
        <Panel title="재설계 회차">
          <Empty>
            회차를 보려면 프로젝트를 고르십시오. 머리말의 프로젝트 선택기에서도 고를 수 있습니다.
          </Empty>
        </Panel>
      )}
    </>
  )
}
