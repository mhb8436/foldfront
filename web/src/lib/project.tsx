import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

import { api, type Project, type Round } from '../api/client'

/**
 * Which project the console is looking at.
 *
 * Every screen that lists runs asks this rather than deciding for itself, so
 * the header selector and what the screens show can never disagree - the
 * header is the only place the choice is made, and it is always visible.
 * A filter you cannot see is a filter that reads as missing data.
 *
 * "Whole installation" is a real choice, not the absence of one: a run
 * started before projects existed belongs to no project, and hiding those by
 * default would make them unreachable.
 *
 * The choice is remembered per browser. It is a convenience, not state the
 * server needs, so it lives in localStorage and a browser that refuses to
 * store it simply starts from the whole installation each time.
 *
 * `?project=<id>` on any screen opens that project - the way you send someone
 * a link to what you are looking at. It is read on arrival and not written
 * back: the address says where you were sent, storage remembers where you
 * were, and an address that rewrote itself on every click would make the
 * browser's back button walk through project selections.
 */

const REMEMBERED = 'foldfront.project'

interface Ctx {
  projects: Project[]
  /** Null means the whole installation. */
  current: Project | null
  /** Passed straight to the API; undefined means do not filter. */
  filter: string | undefined
  select: (projectId: string | null) => void
  rounds: Round[]
  loading: boolean
  reload: () => void
}

const ProjectContext = createContext<Ctx>({
  projects: [],
  current: null,
  filter: undefined,
  select: () => {},
  rounds: [],
  loading: true,
  reload: () => {},
})

function remembered(): string | null {
  try {
    return window.localStorage.getItem(REMEMBERED)
  } catch {
    return null
  }
}

function remember(projectId: string | null) {
  try {
    if (projectId) window.localStorage.setItem(REMEMBERED, projectId)
    else window.localStorage.removeItem(REMEMBERED)
  } catch {
    //  Private browsing, or storage turned off. The selection still works for
    //  this session; it just does not survive a reload.
  }
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [rounds, setRounds] = useState<Round[]>([])
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let alive = true
    api
      .listProjects()
      .then((r) => {
        if (!alive) return
        setProjects(r.items)
        //  A remembered project that has since been deleted falls back to the
        //  whole installation rather than filtering everything away.
        const asked = new URLSearchParams(window.location.search).get('project')
        const wanted = asked ?? remembered()
        const known = wanted && r.items.some((p) => p.project_id === wanted)
        setCurrentId(known ? wanted : null)
        //  A link that named a project is also a choice worth remembering.
        if (known && asked) remember(asked)
      })
      .catch(() => alive && setProjects([]))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [nonce])

  useEffect(() => {
    if (!currentId) {
      setRounds([])
      return
    }
    let alive = true
    api
      .listRounds(currentId)
      .then((r) => alive && setRounds(r.items))
      .catch(() => alive && setRounds([]))
    return () => {
      alive = false
    }
  }, [currentId, nonce])

  const select = useCallback((projectId: string | null) => {
    setCurrentId(projectId)
    remember(projectId)
  }, [])

  const current = projects.find((p) => p.project_id === currentId) ?? null

  return (
    <ProjectContext.Provider
      value={{
        projects,
        current,
        filter: current?.project_id,
        select,
        rounds,
        loading,
        reload: () => setNonce((n) => n + 1),
      }}
    >
      {children}
    </ProjectContext.Provider>
  )
}

export function useProject() {
  return useContext(ProjectContext)
}

/** How a round is named on screen. The index is what people say out loud. */
export function roundLabel(round: Round): string {
  return round.name?.trim() || `${round.index}차 설계`
}
