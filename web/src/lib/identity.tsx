import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { api, type Identity } from '../api/client'

/**
 * Who is using the console, and what the screens should therefore offer.
 *
 * Hiding a control is a courtesy, not a check: the server enforces permission
 * on every write path whatever the console draws. What this avoids is offering
 * someone a button that will answer 403 - which reads as a broken product
 * rather than as a permission they do not have.
 *
 * Until the answer arrives, nothing is assumed. `can` is false, so no
 * write control flashes into view and then disappears.
 */

export type Role = 'admin' | 'researcher' | 'viewer' | 'service'

interface Ctx {
  identity: Identity | null
  loading: boolean
  /** Whether the caller holds any of these roles. */
  is: (...roles: Role[]) => boolean
  /** May start runs, edit workflows, compare results. */
  canRun: boolean
  /** May register models, approve them, read the audit trail. */
  canAdmin: boolean
}

const IdentityContext = createContext<Ctx>({
  identity: null,
  loading: true,
  is: () => false,
  canRun: false,
  canAdmin: false,
})

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api
      .me()
      .then((v) => alive && setIdentity(v))
      //  An identity that cannot be read is treated as no identity. The screens
      //  then show what a viewer sees, which is the safe direction to fail in.
      .catch(() => alive && setIdentity(null))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  const is = (...roles: Role[]) => (identity?.roles ?? []).some((r) => roles.includes(r as Role))

  return (
    <IdentityContext.Provider
      value={{
        identity,
        loading,
        is,
        canRun: is('admin', 'researcher'),
        canAdmin: is('admin'),
      }}
    >
      {children}
    </IdentityContext.Provider>
  )
}

export function useIdentity() {
  return useContext(IdentityContext)
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: '운영자',
  researcher: '연구자',
  viewer: '조회자',
  service: '연계 계정',
}
