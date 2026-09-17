import { useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { api, type UserAccount } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Notice, Panel, StatusDot, formatTime } from '../components/Common'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ROLE_LABEL, type Role, useIdentity } from '@/lib/identity'
import { cn } from '@/lib/utils'

/**
 * Accounts, and what an operator has decided about them.
 *
 * The identity provider says who someone is and, coarsely, what they are -
 * admin or user, nothing finer. This screen is where the rest is decided:
 * that this person is a viewer here, that this account is a service account,
 * that it is switched off. What is set here wins over what the provider said.
 *
 * Every account that has signed in is listed, because it was recorded on its
 * first sign-in; there is nothing to add by hand.
 */

const ROLES: Role[] = ['admin', 'researcher', 'viewer', 'service']

export function Users() {
  const users = useAsync(() => api.listUsers(), [])
  const { identity } = useIdentity()
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  //  A row's switches are built from the row as last fetched. Two clicks
  //  before the first answers would each send a list missing the other's
  //  change, so a row takes one change at a time.
  const [busy, setBusy] = useState<string | null>(null)

  async function toggleRole(u: UserAccount, role: Role) {
    const roles = u.roles.includes(role) ? u.roles.filter((r) => r !== role) : [...u.roles, role]
    await apply(u, { roles })
  }

  async function apply(u: UserAccount, patch: { roles?: string[]; active?: boolean }) {
    if (busy) return
    setBusy(u.user_id)
    setError(null)
    setMessage(null)
    try {
      const updated = await api.patchUser(u.user_id, patch)
      setMessage(
        `${u.user_id} — ${updated.active ? updated.roles.map((r) => ROLE_LABEL[r as Role] ?? r).join(' · ') || '역할 없음' : '꺼짐'}`,
      )
      users.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <PageHeader
        title="이용자"
        description="들어온 적 있는 계정과 그 권한입니다. 여기서 정한 것이 인증 공급자가 준 역할보다 우선합니다."
        actions={
          <Button variant="outline" size="sm" onClick={() => users.reload()}>
            <RefreshCw />
            새로고침
          </Button>
        }
      />
      <ErrorBox message={error ?? users.error} />
      <Notice message={message} />

      <Panel
        title="계정"
        description={`${users.data?.count ?? 0}명`}
        bodyClassName={users.data?.items.length ? 'p-0' : undefined}
      >
        {users.data?.items.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[200px]">계정</TableHead>
                <TableHead>역할</TableHead>
                <TableHead className="w-[150px]">마지막 접속</TableHead>
                <TableHead className="w-[110px]">상태</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.data.items.map((u) => {
                const me = u.user_id === identity?.user_id
                return (
                  <TableRow key={u.user_id} className={cn(!u.active && 'opacity-60')}>
                    <TableCell>
                      <div className="font-mono text-[12.5px]">
                        {u.user_id}
                        {me && <span className="text-muted-foreground ml-2 font-sans text-[11px]">나</span>}
                      </div>
                      {u.email && <div className="text-muted-foreground text-[11.5px]">{u.email}</div>}
                    </TableCell>
                    <TableCell>
                      {/*  Each role is its own switch. A select would force one, and
                          an account can be both a researcher and a service caller. */}
                      <div className="flex flex-wrap gap-1.5">
                        {ROLES.map((role) => {
                          const on = u.roles.includes(role)
                          return (
                            <button
                              key={role}
                              type="button"
                              role="switch"
                              aria-checked={on}
                              aria-label={`${u.user_id} ${ROLE_LABEL[role]}`}
                              //  Your own operator role is not yours to remove: the
                              //  server refuses it, and a switch that answers 400 is
                              //  worse than one that is not offered.
                              disabled={!u.active || busy === u.user_id || (me && role === 'admin' && on)}
                              onClick={() => toggleRole(u, role)}
                              className={cn(
                                'rounded-md border px-2 py-0.5 text-[12px] transition-colors',
                                on ? 'bg-foreground text-background border-foreground' : 'text-muted-foreground hover:bg-accent',
                                'disabled:cursor-not-allowed',
                              )}
                            >
                              {ROLE_LABEL[role]}
                            </button>
                          )
                        })}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular text-[12.5px] whitespace-nowrap">
                      {formatTime(u.last_login_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        //  Switching yourself off is the one thing this screen refuses
                        //  to offer: the server would refuse the last operator anyway,
                        //  and a button that answers 409 is worse than no button.
                        disabled={me || busy === u.user_id}
                        onClick={() => apply(u, { active: !u.active })}
                      >
                        <StatusDot status={u.active ? 'succeeded' : 'cancelled'} />
                        {u.active ? '켜짐' : '꺼짐'}
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        ) : (
          <Empty>아직 들어온 계정이 없습니다. 처음 로그인하면 여기에 기록됩니다.</Empty>
        )}
      </Panel>
    </>
  )
}
