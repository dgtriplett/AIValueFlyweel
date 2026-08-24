// The app's front door: wordmark, grouped nav, generation tools, and console link.

import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  Archive,
  BarChart3,
  BookOpen,
  CalendarRange,
  Database,
  FileText,
  GitBranch,
  HandCoins,
  Layers,
  LineChart,
  Network,
  Palette,
  Radar,
  Rocket,
  Search,
  Sparkles,
  Settings,
  Shield,
  SlidersVertical,
  Tags,
  Users,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useRole, usePersona, type Persona } from '../context/RoleContext'

export type TabId =
  | 'portfolio'
  | 'registry'
  | 'flywheel'
  | 'dashboards'
  | 'roadmap'
  | 'funding'
  | 'value'
  | 'coverage'
  | 'whatif'
  | 'trend'
  | 'glossary'
  | 'artifacts'
  | 'executive'
  | 'knowledge'
  | 'sourcemapping'
  | 'taxonomy'
  | 'rules'
  | 'generate'
  | 'roadmap_import'
  | 'proposals'
  | 'research'
  | 'accounts'
  | 'admin'
  | 'branding'
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
    id: 'coverage',
    label: 'Coverage',
    icon: <BarChart3 className="w-4 h-4" />,
    hint: 'Where the portfolio is covered, exposed or blocked',
  },
  {
    id: 'whatif',
    label: 'What-if Analysis',
    icon: <GitBranch className="w-4 h-4" />,
    hint: 'How portfolio outcomes change when assumptions move',
  },
  {
    id: 'trend',
    label: 'Trend Analysis',
    icon: <LineChart className="w-4 h-4" />,
    hint: 'How portfolio value and readiness change over time',
  },
  {
    id: 'glossary',
    label: 'Glossary',
    icon: <BookOpen className="w-4 h-4" />,
    hint: 'The shared language behind the portfolio',
  },
  {
    id: 'artifacts',
    label: 'Artifacts',
    icon: <Archive className="w-4 h-4" />,
    hint: 'The documents and evidence attached to the work',
  },
  {
    id: 'executive',
    label: 'Executive Brief',
    icon: <FileText className="w-4 h-4" />,
    hint: 'The planning decisions and funding story leaders need',
  },
  {
    id: 'knowledge',
    label: 'Knowledge Base',
    icon: <Database className="w-4 h-4" />,
    hint: 'The standards, studies and runbooks behind the work',
  },
  {
    id: 'sourcemapping',
    label: 'Source Mapping',
    icon: <Network className="w-4 h-4" />,
    hint: 'How source material connects to the model',
  },
  {
    id: 'taxonomy',
    label: 'Taxonomy',
    icon: <Tags className="w-4 h-4" />,
    hint: 'The categories that keep knowledge consistent',
  },
  {
    id: 'rules',
    label: 'Rules',
    icon: <Shield className="w-4 h-4" />,
    hint: 'The policies that govern how knowledge is curated',
  },
  {
    id: 'generate',
    label: 'Generate use cases',
    icon: <Sparkles className="w-4 h-4" />,
    hint: 'Propose use cases grounded in the data already landed',
  },
  {
    id: 'roadmap_import',
    label: 'Import roadmap',
    icon: <CalendarRange className="w-4 h-4" />,
    hint: 'Bring a maturity-assessment roadmap into the portfolio',
  },
  {
    id: 'proposals',
    label: 'Write a proposal',
    icon: <FileText className="w-4 h-4" />,
    hint: 'Generate an eight-section proposal grounded in this instance',
  },
  {
    id: 'research',
    label: 'Research',
    icon: <Search className="w-4 h-4" />,
    hint: 'The evidence behind value drivers and assumptions',
  },
  {
    id: 'accounts',
    label: 'Accounts',
    icon: <Users className="w-4 h-4" />,
    hint: 'Manage the organizations connected to this workspace',
  },
  {
    id: 'admin',
    label: 'Administration',
    icon: <Settings className="w-4 h-4" />,
    hint: 'Control workspace access and operational settings',
  },
  {
    id: 'branding',
    label: 'Branding',
    icon: <Palette className="w-4 h-4" />,
    hint: 'Shape the workspace identity customers see',
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
 * is now a short set of jobs — see the portfolio, plan and fund it, check what
 * the numbers rest on, work with knowledge, or configure the workspace — so the
 * choice at the top is about what you are trying to do, not which screen holds it.
 *
 * `onboarding` is deliberately NOT in a group: it is an entry-point surface,
 * rendered as its own top-level button so a customer with an empty instance can
 * find it without opening a menu. It was previously in neither TABS nor a group,
 * which left OnboardingView reachable only by editing state by hand.
 */
const ALL_NAV_GROUPS: { label: string; ids: TabId[] }[] = [
  {
    label: 'Portfolio',
    ids: ['portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'whatif', 'trend'],
  },
  { label: 'Plan & Fund', ids: ['roadmap', 'funding', 'executive'] },
  { label: 'Value', ids: ['value', 'research'] },
  {
    label: 'Knowledge',
    ids: ['knowledge', 'glossary', 'taxonomy', 'sourcemapping', 'rules', 'artifacts'],
  },
  { label: 'Settings', ids: ['accounts', 'admin', 'branding'] },
]

/** The entry-point surface: outside the groups, always one click away. */
const ENTRY_TAB: TabId = 'onboarding'
const TOOL_TABS: TabId[] = ['generate', 'roadmap_import', 'proposals']

/**
 * PHASE 2: PERSONA-AWARE NAVIGATION FILTERING
 *
 * Returns the subset of nav groups + tabs visible to the given persona.
 *
 * ADMIN sees EVERYTHING (no filtering).
 *
 * PM (portfolio manager) sees:
 *   - Portfolio group: all tabs including 'registry' (data asset management)
 *   - Plan & Fund: roadmap, funding (NOT executive — that's for executives)
 *   - Value: both tabs
 *   - Knowledge: knowledge, glossary, artifacts (READ surfaces only — NOT
 *     sourcemapping, taxonomy, rules, which are admin-only CURATION)
 *   - Generation tools: generate, proposals, roadmap_import
 *   - Onboarding
 *   - NO Settings
 *
 * EXECUTIVE sees MINIMAL read-only set:
 *   - dashboards (from Portfolio)
 *   - executive (from Plan & Fund)
 *   - roadmap (from Plan & Fund)
 *   - portfolio (from Portfolio, read-only)
 *   - NOTHING else
 */
function filterNavForPersona(persona: Persona): {
  groups: { label: string; ids: TabId[] }[]
  entryTab: TabId | null
  toolTabs: TabId[]
} {
  if (persona === 'admin') {
    // Admin sees EVERYTHING — no filtering.
    return { groups: ALL_NAV_GROUPS, entryTab: ENTRY_TAB, toolTabs: TOOL_TABS }
  }

  if (persona === 'executive') {
    // Executive sees MINIMAL read-only set: dashboards, executive, roadmap, portfolio.
    // Presented as two groups for clarity.
    return {
      groups: [
        { label: 'Portfolio', ids: ['portfolio', 'dashboards'] },
        { label: 'Plan & Fund', ids: ['roadmap', 'executive'] },
      ],
      entryTab: null,
      toolTabs: [],
    }
  }

  // PM sees:
  //   - Full Portfolio group (all tabs including registry)
  //   - Plan & Fund: roadmap, funding (NOT executive)
  //   - Value: both tabs
  //   - Knowledge: read surfaces only (knowledge, glossary, artifacts — NOT curation)
  //   - All generation tools
  //   - Onboarding
  return {
    groups: [
      {
        label: 'Portfolio',
        ids: ['portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'whatif', 'trend'],
      },
      { label: 'Plan & Fund', ids: ['roadmap', 'funding'] },
      { label: 'Value', ids: ['value', 'research'] },
      { label: 'Knowledge', ids: ['knowledge', 'glossary', 'artifacts'] },
    ],
    entryTab: ENTRY_TAB,
    toolTabs: TOOL_TABS,
  }
}

/** Collect all TabIds that are visible to a persona, for fallback logic. */
export
function visibleTabsForPersona(persona: Persona): Set<TabId> {
  const filtered = filterNavForPersona(persona)
  const ids = filtered.groups.flatMap((g) => g.ids)
  if (filtered.entryTab) ids.push(filtered.entryTab)
  ids.push(...filtered.toolTabs)
  return new Set(ids)
}

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
  //
  // It still dismisses an open menu, like a grouped item does. The click-outside
  // handler is scoped to the nav element, so it deliberately does NOT fire for a
  // button that lives inside the nav — without this, picking a direct button
  // would change the view and leave another group's menu hanging open over it.
  const only = ids.length === 1 ? TABS.find((candidate) => candidate.id === ids[0]) : undefined
  if (only) {
    return (
      <TabButton
        tab={only}
        label={label}
        active={active}
        onSelect={() => {
          setOpenMenu(null)
          setTab(only.id)
        }}
      />
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

export interface HeaderBranding {
  display_name: string
  subtitle: string
  accent_color: string
  logo_url?: string | null
}

export function Header({
  env,
  tab,
  setTab,
  branding,
}: {
  env?: string | null
  tab: TabId
  setTab: (id: TabId) => void
  branding?: HeaderBranding | null
}) {
  const activePersona = usePersona()
  const { groups: NAV_GROUPS, entryTab, toolTabs } = filterNavForPersona(activePersona)
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

  const entry = entryTab ? TABS.find((candidate) => candidate.id === entryTab) : null

  // Use branding when available, fall back to defaults
  const displayName = branding?.display_name ?? 'AI Value Flywheel'
  const subtitle = branding?.subtitle ?? 'Power & Utilities — Data & AI Catalog, Value & Roadmap'
  const accentColor = branding?.accent_color ?? '#FF3621'
  const logoUrl = branding?.logo_url

  return (
    <header className="border-b border-navy-600 bg-navy-800/90 backdrop-blur sticky top-0 z-30">
      <div className="max-w-[1440px] mx-auto px-6">
        <div className="flex items-center justify-between py-3">
          <div className="flex items-center gap-3">
            {logoUrl ? (
              <img
                src={logoUrl}
                alt="Logo"
                className="max-h-8 rounded"
                style={{ maxWidth: '120px' }}
              />
            ) : null}
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white">
                {displayName.includes('Flywheel') ? (
                  <>
                    {displayName.split('Flywheel')[0]}
                    <span style={{ color: accentColor }}>Flywheel</span>
                    {displayName.split('Flywheel')[1] || ''}
                  </>
                ) : (
                  displayName
                )}
              </h1>
              <p className="text-xs text-navy-400 -mt-0.5">{subtitle}</p>
            </div>
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
            <PersonaSwitcher />
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
            <TabButton
              tab={entry}
              active={tab === entry.id}
              onSelect={() => {
                setOpenMenu(null)
                setTab(entry.id)
              }}
            />
          ) : null}

          <div className="ml-auto flex items-center gap-1">
            {toolTabs.map((id) => {
              const item = TABS.find((candidate) => candidate.id === id)
              if (!item) return null
              return (
                <TabButton
                  key={id}
                  tab={item}
                  active={tab === id}
                  onSelect={() => {
                    setOpenMenu(null)
                    setTab(id)
                  }}
                />
              )
            })}
          </div>

          {CONSOLE_LINKS.map((link) => (
            <a
              key={link.key}
              href={link.href}
              title={link.title}
              className={`${NAV_ITEM} ${NAV_INACTIVE}`}
            >
              {link.label}
            </a>
          ))}
        </nav>
      </div>
    </header>
  )
}

/**
 * Minimal persona switcher for Phase 1. Lets users self-select their persona
 * ('admin' | 'pm' | 'executive') unless they're exec-locked.
 *
 * Phase 1: establishes the switcher; the UI does NOT yet react to persona.
 * Phase 2+: nav/views adapt based on activePersona.
 */
function PersonaSwitcher() {
  const { activePersona, setPersona, isExecLocked, loading } = useRole()

  if (loading) return null
  // Phase 2: hide switcher for exec-locked users (they cannot switch anyway)
  if (isExecLocked) return null

  const personas: Array<{ value: Persona; label: string }> = [
    { value: 'admin', label: 'Admin' },
    { value: 'pm', label: 'PM' },
    { value: 'executive', label: 'Executive' },
  ]

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-navy-400">Persona:</span>
      <select
        value={activePersona}
        onChange={(e) => setPersona(e.target.value as Persona)}
        disabled={isExecLocked}
        className="text-xs bg-navy-700 text-navy-200 border border-navy-600 rounded px-2 py-1 disabled:opacity-50 disabled:cursor-not-allowed"
        title={isExecLocked ? 'Persona is locked by admin' : 'Switch persona'}
      >
        {personas.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  )
}
