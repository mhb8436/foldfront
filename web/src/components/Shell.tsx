import { useEffect, useState, type ComponentType, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  BarChart3,
  Dna,
  FolderOpen,
  LayoutGrid,
  Layers,
  MessageSquare,
  Moon,
  PanelLeft,
  Play,
  Settings2,
  Sun,
  UsersRound,
  Workflow,
} from 'lucide-react'

import { api } from '../api/client'
import { branding } from '@/lib/branding'
import { ROLE_LABEL, type Role, useIdentity } from '@/lib/identity'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { StatusDot } from './Common'
import { Notices } from './Notices'
import { ProjectPicker } from './ProjectPicker'

/**
 * The console shell.
 *
 * Header, left navigation, content and footer, shared by every screen. The
 * frame does not change from one screen to the next, so it is learned once.
 */

type Item = {
  to?: string
  label: string
  icon?: ComponentType<{ className?: string }>
  badge?: string
  children?: Item[]
  /** Hidden unless the caller holds one of these. Absent means everyone. */
  roles?: Role[]
}

const NAV: Array<{ section: string; items: Item[] }> = [
  {
    section: '설계',
    items: [
      { to: '/dashboard', label: '대시보드', icon: LayoutGrid },
      { to: '/projects', label: '프로젝트', icon: FolderOpen },
      { to: '/studio', label: '워크플로 스튜디오', icon: Workflow },
      {
        label: '설계 실행',
        icon: Play,
        children: [
          { to: '/setup', label: '실행 준비' },
          { to: '/monitor', label: '실행 감시' },
        ],
      },
      { to: '/analyze', label: '결과 분석', icon: BarChart3 },
    ],
  },
  {
    section: '관리',
    items: [
      { to: '/models', label: '모델 관리', icon: Layers },
      { to: '/operations', label: '운영', icon: Settings2, roles: ['admin'] },
      { to: '/users', label: '이용자', icon: UsersRound, roles: ['admin'] },
      { to: '/copilot', label: '설계 Copilot', icon: MessageSquare },
    ],
  },
]

function useDarkMode() {
  const [dark, setDark] = useState(() => {
    try {
      return localStorage.getItem('console-theme') === 'dark'
    } catch {
      return false
    }
  })
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try {
      localStorage.setItem('console-theme', dark ? 'dark' : 'light')
    } catch {
      //  Private browsing blocks the write. Nothing on screen depends on it.
    }
  }, [dark])
  return [dark, setDark] as const
}

/** Backend health. Polls /healthz so the indicator means something. */
function useHealth() {
  const [ok, setOk] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    const check = () =>
      api
        .health()
        .then((h: { status?: string }) => alive && setOk(h?.status === 'ok'))
        .catch(() => alive && setOk(false))
    check()
    const timer = setInterval(check, 30_000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])
  return ok
}

function NavItem({ item, collapsed }: { item: Item; collapsed: boolean }) {
  const Icon = item.icon
  const base =
    'flex h-9 items-center gap-2.5 rounded-md px-2.5 text-[13.5px] transition-colors'

  if (!item.to) {
    return (
      <div
        className={cn(base, 'text-muted-foreground/70 cursor-default')}
        title={collapsed ? item.label : undefined}
      >
        {Icon && <Icon className="size-4 shrink-0" />}
        {!collapsed && (
          <>
            <span className="truncate">{item.label}</span>
            {item.badge && (
              <Badge variant="muted" className="ml-auto">
                {item.badge}
              </Badge>
            )}
          </>
        )}
      </div>
    )
  }

  return (
    <NavLink
      to={item.to}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          base,
          isActive
            ? 'bg-accent text-accent-foreground font-semibold'
            : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
        )
      }
    >
      {Icon && <Icon className="size-4 shrink-0" />}
      {!collapsed && <span className="truncate">{item.label}</span>}
    </NavLink>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const [dark, setDark] = useDarkMode()
  const health = useHealth()
  const { pathname } = useLocation()
  const { identity, is } = useIdentity()
  const role = identity?.roles[0] as Role | undefined

  return (
    <div className="flex min-h-screen flex-col">
      {/* ───────── header ───────── */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label="내비게이션 접기"
          onClick={() => setCollapsed((v) => !v)}
        >
          <PanelLeft className="size-4" />
        </Button>

        <div className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md">
          <Dna className="size-4" />
        </div>
        <span className="text-[14.5px] font-semibold tracking-tight">{branding.title}</span>
        {branding.organization && (
          <>
            <Separator orientation="vertical" className="h-4" />
            <span className="text-muted-foreground hidden text-[12.5px] lg:inline">
              {branding.organization}
            </span>
          </>
        )}

        <div className="flex-1" />

        <ProjectPicker />

        <div className="text-muted-foreground flex items-center gap-1.5 text-[12.5px]">
          <StatusDot status={health === null ? 'pending' : health ? 'succeeded' : 'failed'} />
          <span className="hidden sm:inline">
            {health === null ? '확인 중' : health ? '정상' : '점검'}
          </span>
        </div>

        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label={dark ? '밝은 화면으로' : '어두운 화면으로'}
          onClick={() => setDark(!dark)}
        >
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
        <Notices />
        {/*  Who is signed in, and as what. Previously a fixed letter and a
            fixed label, which said nothing and could be wrong. */}
        <div className="flex items-center gap-2">
          <div className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-full text-[12px] font-semibold uppercase">
            {(identity?.user_id ?? '?').slice(0, 1)}
          </div>
          <div className="hidden flex-col leading-tight lg:flex">
            <span className="text-[12.5px] font-medium">{identity?.user_id ?? '—'}</span>
            <span className="text-muted-foreground text-[11px]">
              {role ? ROLE_LABEL[role] : '권한 없음'}
              {identity && !identity.authenticated && ' · 인증 꺼짐'}
            </span>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ───────── left navigation ───────── */}
        <nav
          className={cn(
            'bg-muted/40 hidden shrink-0 flex-col border-r px-2.5 py-3 transition-[width] md:flex',
            collapsed ? 'w-[60px]' : 'w-[236px]',
          )}
        >
          {NAV.map((group) => (
            <div key={group.section} className="mb-1">
              {!collapsed && (
                <div className="text-muted-foreground/70 px-2.5 pt-3 pb-1.5 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
                  {group.section}
                </div>
              )}
              {collapsed && <Separator className="my-2" />}
              <div className="flex flex-col gap-0.5">
                {group.items
                  .filter((item) => !item.roles || is(...item.roles))
                  .map((item) =>
                  item.children ? (
                    <div key={item.label} className="flex flex-col gap-0.5">
                      <NavItem item={{ ...item, to: undefined, badge: undefined }} collapsed={collapsed} />
                      {!collapsed &&
                        item.children.map((child) => (
                          <NavLink
                            key={child.to}
                            to={child.to!}
                            className={({ isActive }) =>
                              cn(
                                'flex h-8 items-center rounded-md pr-2.5 pl-9 text-[13px] transition-colors',
                                isActive
                                  ? 'bg-accent text-accent-foreground font-semibold'
                                  : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                              )
                            }
                          >
                            {child.label}
                          </NavLink>
                        ))}
                      {collapsed &&
                        item.children.map((child) => (
                          <NavItem
                            key={child.to}
                            item={{ ...child, icon: item.icon }}
                            collapsed={collapsed}
                          />
                        ))}
                    </div>
                  ) : (
                    <NavItem key={item.label} item={item} collapsed={collapsed} />
                  ),
                )}
              </div>
            </div>
          ))}
        </nav>

        {/* ───────── content ───────── */}
        <main key={pathname} className="min-w-0 flex-1 px-6 py-6 lg:px-7">
          <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-5">{children}</div>
        </main>
      </div>

      {/* ───────── footer ───────── */}
      <footer className="text-muted-foreground flex h-11 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t px-6 text-[11.5px]">
        <span>{branding.copyright || `© ${new Date().getFullYear()} ${branding.product}`}</span>
        <span className="hidden sm:inline">·</span>
        <span className="hidden sm:inline">승계 {branding.upstream}</span>
        <span className="flex-1" />
        <span className="font-mono">
          {__APP_VERSION__} · {__APP_COMMIT__}
        </span>
      </footer>
    </div>
  )
}

/** A page heading: title, description and actions in one row. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-muted-foreground text-[13.5px]">{description}</p>}
      </div>
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  )
}
