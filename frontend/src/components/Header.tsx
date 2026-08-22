// The app's front door: wordmark, the grouped nav, and the links out to /console.

import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  BarChart3,
  CalendarRange,
  Database,
  HandCoins,
  Layers,
  Radar,
  Rocket,
  SlidersVertical,
} from 'lucide-react'
import type { ReactNode } from 'react'

export type TabId =
  | 'portfolio'
  | 'registry'
  | 'flywheel'
  | 'dashboards'
  | 'roadmap'
  | 'funding'
  | 'value'
  | 'onboarding'

export interface Tab {
  id: TabId
  label: string
  icon: ReactNode
  /** Shown under the label inside a menu: what you come to this view to answer. */
  hint?: string
}

/**
 * Every view the nav can reach. Kept as data, not markup, so the grouped nav
 * below resolves tabs by id — a reordered or extended TABS still renders, and
 * tab state stays the single source of truth for what is on screen.
 *
 * There is no separate `catalog` tab: the catalog is the same use-case list read
 * at a different scope, so it is a switch inside `portfolio` rather than a
 * destination of its own. See components/ScopeSwitch.tsx.
 */
export const TABS: Tab[] = [
  {
    id: 'portfolio',
    label: 'Use Cases',
    icon: <Layers className="w-4 h-4" />,
    hint: 'Your portfolio and the catalog to draw from',
  },
  {
    id: 'flywheel',
    label: 'Value Flywheel',
    icon: <Radar className="w-4 h-4" />,
    hint: 'What unlocks what, and the blast radius of each source',
  },
  {
    id: 'registry',
    label: 'Data Assets',
    icon: <Database className="w-4 h-4" />,
    hint: 'The sources behind the work, and their ingestion state',
  },
  {
    id: 'dashboards',
    label: 'Dashboards',
    icon: <BarChart3 className="w-4 h-4" />,
    hint: 'Value, readiness and coverage rolled up',
  },
  {
    id: 'roadmap',
    label: 'Roadmap',
    icon: <CalendarRange className="w-4 h-4" />,
    hint: 'Sequence the work by what is ready',
  },
  {
    id: 'funding',
    label: 'Joint Funding',
    icon: <HandCoins className="w-4 h-4" />,
    hint: 'Split investment against the value it buys',
  },
  {
    id: 'value',
    label: 'Value & Assumptions',
    icon: <SlidersVertical className="w-4 h-4" />,
    hint: 'The drivers every number on screen is computed from',
  },
  {
    id: 'onboarding',
    label: 'Get started',
    icon: <Rocket className="w-4 h-4" />,
    hint: 'Populate the model from Excel or your own workspace',
  },
]

/**
 * The nav groups, named for the JOB rather than for the screens inside them.
 *
 * The flat list read as eight unrelated destinations, two of which
 * ("Portfolio", "Use Case Catalog") were one list at two scopes. The top level
 * is now three jobs — see the portfolio, plan and fund it, check what the
 * numbers rest on — so the choice at the top is about what you are trying to do,
 * not which screen holds it.
 *
 * `onboarding` is deliberately NOT in a group: it is an entry-point surface,
 * rendered as its own top-level button so a customer with an empty instance can
 * find it without opening a menu. It was previously in neither TABS nor a group,
 * which left OnboardingView reachable only by editing state by hand.
 */
const NAV_GROUPS: { label: string; ids: TabId[] }[] = [
  { label: 'Portfolio', ids: ['portfolio', 'flywheel', 'registry', 'dashboards'] },
  { label: 'Plan & Fund', ids: ['roadmap', 'funding'] },
  { label: 'Value', ids: ['value'] },
]

/** The entry-point surface: outside the groups, always one click away. */
const ENTRY_TAB: TabId = 'onboarding'

/** The console is a separate dependency-free page; these are its entry points. */
const CONSOLE_LINKS = [
  {
    key: 'kb-link',
    href: '/console/#kb',
    label: 'Knowledge base',
    title:
      'Standards, proposals, studies and runbooks — attached to the use cases they explain',
    marginLeftAuto: true,
  },
  {
    key: 'prop-link',
    href: '/console/#proposals',
    label: 'Write a proposal',
    title:
      'Generate an eight-section proposal for a use case, grounded in this instance’s own data',
    marginLeftAuto: false,
  },
  {
    key: 'console-link',
    href: '/console/',
    label: '⚙ Set up & discover',
    title:
      'Set up, load your data, discover your estate, review coverage, and generate use cases',
    marginLeftAuto: false,
  },
]

const NAV_ITEM =
  'px-4 py-2.5 text-sm font-medium flex items-center gap-2 border-b-2 transition-colors'
const NAV_INACTIVE = 'border-transparent text-navy-400 hover:text-navy-300'
const ACTIVE_STYLE = { borderColor: '#FF3621', color: '#FF9E94' }

function TabButton({
  tab,
  active,
  onSelect,
  label,
}: {
  tab: Tab
  active: boolean
  onSelect: () => void
  /** Overrides the tab's own label, so a one-item group can name itself. */
  label?: string
}) {
  return (
    <button
      onClick={onSelect}
      className={`${NAV_ITEM} ${active ? 'text-white' : NAV_INACTIVE}`}
      style={active ? ACTIVE_STYLE : {}}
      title={tab.hint}
    >
      {tab.icon}
      {label ?? tab.label}
    </button>
  )
}

function NavGroup({
  label,
  ids,
  tab,
  setTab,
  openMenu,
  setOpenMenu,
}: {
  label: string
  ids: TabId[]
  tab: TabId
  setTab: (id: TabId) => void
  openMenu: string | null
  setOpenMenu: (label: string | null) => void
}) {
  const open = openMenu === label
  const active = ids.indexOf(tab) >= 0

  // A group holding one view is a menu with nothing to choose. It stays declared
  // as a group — the grouping is the IA, and a second view may join it — but it
  // renders as a direct button so reaching it never costs an extra click.
  const only = ids.length === 1 ? TABS.find((candidate) => candidate.id === ids[0]) : undefined
  if (only) {
    return (
      <TabButton tab={only} label={label} active={active} onSelect={() => setTab(only.id)} />
    )
  }

  return (
    <div className="relative">
      <button
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpenMenu(open ? null : label)}
        className={`${NAV_ITEM} ${active ? 'text-white' : NAV_INACTIVE}`}
        style={active ? ACTIVE_STYLE : {}}
      >
        {label}
        <span style={{ fontSize: '9px', opacity: 0.6 }}>▾</span>
      </button>
      <div
        role="menu"
        aria-label={label}
        data-ga-menu="1"
        style={{
          display: open ? 'block' : 'none',
          position: 'absolute',
          top: '100%',
          left: 0,
          zIndex: 200,
          // Wide enough for the longest hint line, so neither a label nor its
          // one-line description wraps inside the panel.
          minWidth: '318px',
          padding: '6px',
          background: '#1b3139',
          border: '1px solid #2d4550',
          borderRadius: '10px',
          boxShadow: '0 18px 44px rgba(0,0,0,.62)',
        }}
      >
        {ids.map((id) => {
          const item = TABS.find((candidate) => candidate.id === id)
          if (!item) return null
          const selected = tab === id
          return (
            <button
              key={id}
              role="menuitem"
              onClick={() => {
                setOpenMenu(null)
                setTab(id)
              }}
              className={
                'w-full text-left px-3 py-2 rounded-md text-sm font-medium ' +
                'flex items-start gap-2 whitespace-nowrap ' +
                (selected ? 'text-white' : 'text-navy-300 hover:text-white hover:bg-navy-700')
              }
              style={selected ? { color: '#FF9E94' } : {}}
            >
              <span className="mt-0.5 shrink-0">{item.icon}</span>
              <span>
                {item.label}
                {item.hint ? (
                  <span className="block text-[11px] font-normal text-navy-500 mt-0.5">
                    {item.hint}
                  </span>
                ) : null}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function Header({
  env,
  tab,
  setTab,
}: {
  env?: string | null
  tab: TabId
  setTab: (id: TabId) => void
}) {
  const [openMenu, setOpenMenu] = useState<string | null>(null)
  const navRef = useRef<HTMLElement | null>(null)

  // A menu must close when you click anything else, including the other trigger,
  // and on Escape. Scoping the pointer check to the nav element means the
  // handler cannot depend on listener order or on a trigger calling
  // stopPropagation — clicking a trigger while another menu is open still lands.
  useEffect(() => {
    if (!openMenu) return
    const onPointerDown = (event: MouseEvent) => {
      if (!navRef.current?.contains(event.target as Node)) setOpenMenu(null)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenu(null)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [openMenu])

  const entry = TABS.find((candidate) => candidate.id === ENTRY_TAB)

  return (
    <header className="border-b border-navy-600 bg-navy-800/90 backdrop-blur sticky top-0 z-30">
      <div className="max-w-[1440px] mx-auto px-6">
        <div className="flex items-center justify-between py-3">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white">
              AI Value <span style={{ color: '#FF3621' }}>Flywheel</span>
            </h1>
            <p className="text-xs text-navy-400 -mt-0.5">
              Power &amp; Utilities — Data &amp; AI Catalog, Value &amp; Roadmap
            </p>
          </div>
          <div className="flex items-center gap-3">
            {env ? (
              <span
                className="badge-muted inline-flex items-center gap-1"
                title="Deployment environment"
              >
                <Activity className="w-3 h-3" />
                {env}
              </span>
            ) : null}
            <span className="text-xs text-navy-500">Powered by Databricks</span>
          </div>
        </div>

        <nav
          ref={navRef}
          className="flex gap-1 items-center"
          data-gaGroupedNav="1"
          aria-label="Main"
        >
          {NAV_GROUPS.map((group) => (
            <NavGroup
              key={group.label}
              label={group.label}
              ids={group.ids}
              tab={tab}
              setTab={setTab}
              openMenu={openMenu}
              setOpenMenu={setOpenMenu}
            />
          ))}

          {entry ? (
            <TabButton tab={entry} active={tab === entry.id} onSelect={() => setTab(entry.id)} />
          ) : null}

          {CONSOLE_LINKS.map((link) => (
            <a
              key={link.key}
              href={link.href}
              title={link.title}
              className={`${NAV_ITEM} ${NAV_INACTIVE}${link.marginLeftAuto ? ' ml-auto' : ''}`}
            >
              {link.label}
            </a>
          ))}
        </nav>
      </div>
    </header>
  )
}
