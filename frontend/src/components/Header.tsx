// The app's front door: wordmark, the grouped nav, and the links out to /console.

import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  BarChart3,
  BookOpen,
  CalendarRange,
  Database,
  HandCoins,
  Layers,
  Radar,
  SlidersVertical,
} from 'lucide-react'
import type { ReactNode } from 'react'

export type TabId =
  | 'portfolio'
  | 'catalog'
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
}

/**
 * Every view the nav can reach. Kept as data, not markup, so the grouped nav
 * below resolves tabs by id — a reordered or extended TABS still renders, and
 * tab state stays the single source of truth for what is on screen.
 */
export const TABS: Tab[] = [
  { id: 'portfolio', label: 'Portfolio', icon: <Layers className="w-4 h-4" /> },
  { id: 'catalog', label: 'Use Case Catalog', icon: <BookOpen className="w-4 h-4" /> },
  { id: 'registry', label: 'Data Assets', icon: <Database className="w-4 h-4" /> },
  { id: 'flywheel', label: 'Value Flywheel', icon: <Radar className="w-4 h-4" /> },
  { id: 'dashboards', label: 'Dashboards', icon: <BarChart3 className="w-4 h-4" /> },
  { id: 'roadmap', label: 'Roadmap', icon: <CalendarRange className="w-4 h-4" /> },
  { id: 'funding', label: 'Joint Funding', icon: <HandCoins className="w-4 h-4" /> },
  { id: 'value', label: 'Value & Assumptions', icon: <SlidersVertical className="w-4 h-4" /> },
]

/**
 * The nav groups, as workflow stages rather than a flat tab row.
 *
 * Eight flat tabs made the app read as eight unrelated screens. Grouping them
 * into what you are DOING — looking at the numbers, or planning the work — keeps
 * the top level to three items with Portfolio, the landing view, always one
 * click away.
 */
const NAV_GROUPS: { label: string; ids: TabId[] }[] = [
  { label: 'Analyze', ids: ['dashboards', 'flywheel', 'value'] },
  { label: 'Plan', ids: ['roadmap', 'funding', 'catalog', 'registry'] },
]

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
}: {
  tab: Tab
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`${NAV_ITEM} ${active ? 'text-white' : NAV_INACTIVE}`}
      style={active ? ACTIVE_STYLE : {}}
    >
      {tab.icon}
      {tab.label}
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
          // Wide enough for "Value & Assumptions", the longest item, so no label
          // wraps inside the panel.
          minWidth: '212px',
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
                'flex items-center gap-2 whitespace-nowrap ' +
                (selected ? 'text-white' : 'text-navy-300 hover:text-white hover:bg-navy-700')
              }
              style={selected ? { color: '#FF9E94' } : {}}
            >
              {item.icon}
              {item.label}
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

  const portfolio = TABS.find((candidate) => candidate.id === 'portfolio')

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
          {portfolio ? (
            <TabButton
              tab={portfolio}
              active={tab === portfolio.id}
              onSelect={() => setTab(portfolio.id)}
            />
          ) : null}

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
