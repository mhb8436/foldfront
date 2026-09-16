/**
 * The accounts screen.
 *
 * What it owes the operator is that each role is a switch, that the account
 * they are signed in as cannot be switched off from here, and that the
 * server's refusal - the last operator - is shown rather than swallowed.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, type UserAccount } from '../../api/client'
import { IdentityProvider } from '@/lib/identity'
import { Users } from '../Users'

const account = (over: Partial<UserAccount> = {}): UserAccount => ({
  user_id: 'dev',
  subject: 'dev',
  email: null,
  display_name: null,
  roles: ['admin'],
  active: true,
  last_login_at: '2026-09-17T01:00:00Z',
  ...over,
})

function show(items: UserAccount[]) {
  vi.spyOn(api, 'listUsers').mockResolvedValue({ items, count: items.length } as never)
  vi.spyOn(api, 'me').mockResolvedValue({
    user_id: 'dev', email: '', roles: ['admin'], authenticated: false, auth_mode: 'disabled',
  } as never)
  render(
    <MemoryRouter>
      <IdentityProvider>
        <Users />
      </IdentityProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('이용자', () => {
  it('들어온 계정과 역할을 낸다', async () => {
    show([account(), account({ user_id: 'kim', roles: ['researcher'] })])
    expect(await screen.findByText('kim')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'kim 연구자' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'kim 운영자' })).not.toBeChecked()
  })

  it('역할을 누르면 그 역할을 더하거나 뺀다', async () => {
    const patch = vi.spyOn(api, 'patchUser').mockResolvedValue(account({ user_id: 'kim', roles: ['researcher', 'service'] }))
    show([account({ user_id: 'kim', roles: ['researcher'] })])
    fireEvent.click(await screen.findByRole('switch', { name: 'kim 연계 계정' }))

    await waitFor(() => expect(patch).toHaveBeenCalledWith('kim', { roles: ['researcher', 'service'] }))
  })

  it('나 자신은 끌 수 없다', async () => {
    show([account()])
    await screen.findByText('dev')
    await waitFor(() => expect(screen.getByRole('button', { name: /켜짐/ })).toBeDisabled())
  })

  it('마지막 운영자 거절은 그대로 보인다', async () => {
    vi.spyOn(api, 'patchUser').mockRejectedValue(
      new ApiError('kim 는 마지막 운영자입니다. 다른 운영자를 먼저 두십시오.', 409, 'user.last_admin', {}),
    )
    show([account({ user_id: 'kim' })])
    fireEvent.click(await screen.findByRole('switch', { name: 'kim 운영자' }))

    expect(await screen.findByText(/마지막 운영자/)).toBeInTheDocument()
  })
})
