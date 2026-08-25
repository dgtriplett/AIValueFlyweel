// The app's front door: wordmark, grouped nav, generation tools, and console link.

import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
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
  | 'atrisk'
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
    id: 'atrisk',
    label: 'At Risk',
    icon: <AlertTriangle className="w-4 h-4" />,
    hint: 'Use cases slipping or overdue, why, and how often',
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
 * `onboarding` is deliberately NOT in a group: it is an entry-point surface. As
 * of the roles Phase A it is no longer rendered inside the main horizontal nav —
 * it is a one-time onboarding action, not a recurring destination, so its button
 * lives in the top-right cluster UNDER the persona/role indicator (see
 * GetStartedButton). Its routing (setTab('onboarding')) is unchanged.
 */
const ALL_NAV_GROUPS: { label: string; ids: TabId[] }[] = [
  {
    label: 'Portfolio',
    ids: ['portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'atrisk', 'whatif', 'trend'],
  },
  { label: 'Plan & Fund', ids: ['roadmap', 'funding', 'executive'] },
  { label: 'Value', ids: ['value', 'research'] },
  {
    label: 'Knowledge',
    ids: ['knowledge', 'glossary', 'taxonomy', 'sourcemapping', 'rules', 'artifacts'],
  },
  { label: 'Settings', ids: ['accounts', 'admin', 'branding'] },
  { label: 'Create', ids: ['generate', 'roadmap_import', 'proposals'] },
]

/** The entry-point surface: outside the groups, one click away in the top-right
 *  cluster (see GetStartedButton). Not part of the main horizontal nav row. */
const ENTRY_TAB: TabId = 'onboarding'

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
 *
 * `entryTab` names whether the persona should see the Get started affordance
 * (admin/pm yes, executive no). Its button now lives in the top-right cluster,
 * but the gating is unchanged.
 */
function filterNavForPersona(persona: Persona): {
  groups: { label: string; ids: TabId[] }[]
  entryTab: TabId | null
} {
  if (persona === 'admin') {
    // Admin sees EVERYTHING — no filtering.
    return { groups: ALL_NAV_GROUPS, entryTab: ENTRY_TAB }
  }

  if (persona === 'executive') {
    // Executive sees MINIMAL read-only set: dashboards, executive, roadmap, portfolio.
    // Presented as two groups for clarity.
    return {
      groups: [
        { label: 'Portfolio', ids: ['portfolio', 'dashboards', 'atrisk'] },
        { label: 'Plan & Fund', ids: ['roadmap', 'executive'] },
      ],
      entryTab: null,
    }
  }

  // PM sees:
  //   - Full Portfolio group (all tabs including registry)
  //   - Plan & Fund: roadmap, funding (NOT executive)
  //   - Value: both tabs
  //   - Knowledge: read surfaces only (knowledge, glossary, artifacts — NOT curation)
  //   - Create: all generation tools (now grouped)
  //   - Onboarding
  return {
    groups: [
      {
        label: 'Portfolio',
        ids: ['portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'atrisk', 'whatif', 'trend'],
      },
      { label: 'Plan & Fund', ids: ['roadmap', 'funding'] },
      { label: 'Value', ids: ['value', 'research'] },
      { label: 'Knowledge', ids: ['knowledge', 'glossary', 'artifacts'] },
      { label: 'Create', ids: ['generate', 'roadmap_import', 'proposals'] },
    ],
    entryTab: ENTRY_TAB,
  }
}

/**
 * PHASE 4 — ADMIN LOCKDOWN: the tabs whose CONTENT requires a trusted admin.
 *
 * These are the workspace-control and knowledge-CURATION surfaces. Even if a
 * non-admin reaches one (a deep link, a forced tab, a stale persona), App.tsx
 * renders a "not authorized" panel instead of the real view — the render half of
 * defense in depth, on top of the server's own require_admin 403s.
 *
 * REFINEMENT: 'registry' (data-asset management) is deliberately NOT here — it is
 * a PM job and stays PM-accessible. Only these six are admin-only.
 */
export const ADMIN_ONLY_TABS: ReadonlySet<TabId> = new Set<TabId>([
  'accounts',
  'admin',
  'sourcemapping',
  'taxonomy',
  'rules',
  'branding',
])

/** Collect all TabIds that are visible to a persona, for fallback logic. */
export function visibleTabsForPersona(persona: Persona): Set<TabId> {
  const filtered = filterNavForPersona(persona)
  const ids = filtered.groups.flatMap((g) => g.ids)
  if (filtered.entryTab) ids.push(filtered.entryTab)
  return new Set(ids)
}

/**
 * SAVED VIEWS — each persona's natural HOME tab: where it lands on a cold load
 * with no remembered tab, and the fallback when a remembered tab is no longer
 * visible to it.
 *
 * The homes are chosen to match what each persona comes to the app to do:
 *   - executive: 'dashboards' — the rolled-up value/readiness view is an
 *     executive's landing surface (their nav leads with Portfolio then this).
 *   - pm: 'portfolio' — the portfolio is a PM's working set.
 *   - admin: 'portfolio' — admins default into the PM working surface, not a
 *     settings screen; Setup/Settings are opened deliberately, not landed on.
 *
 * Derived from `visibleTabsForPersona`, never a second hand-maintained list:
 * the home is asserted to BE one of the persona's visible tabs, so a home can
 * never name a tab the persona cannot reach.
 */
const PERSONA_HOME: Record<Persona, TabId> = {
  executive: 'dashboards',
  pm: 'portfolio',
  admin: 'portfolio',
}

export function defaultTabForPersona(persona: Persona): TabId {
  const home = PERSONA_HOME[persona]
  // Defense in depth: if the chosen home is ever not in the persona's visible
  // set (a future nav edit), fall back to the first tab the persona CAN see
  // rather than landing them on an invisible tab.
  const visible = visibleTabsForPersona(persona)
  if (visible.has(home)) return home
  const first = visible.values().next().value
  return first ?? 'portfolio'
}

/**
 * SAVED VIEWS — resolve the tab a persona should land on given a remembered one.
 *
 * If the stored tab is visible to the persona, honour it — that is the whole
 * point of remembering. Otherwise (unset, or stale/hand-edited to a tab the
 * persona cannot see) fall back to the persona's default home. This reuses
 * `visibleTabsForPersona` rather than duplicating any per-persona list, so it
 * stays correct as the nav evolves.
 */
export function resolveLandingTab(persona: Persona, stored: TabId | null): TabId {
  if (stored && visibleTabsForPersona(persona).has(stored)) return stored
  return defaultTabForPersona(persona)
}

/** The console is a separate dependency-free page; these are its entry points. */
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

/**
 * ROLES PHASE C — "View as persona" testing switcher for admins.
 *
 * The point of this phase: make the admin persona switcher an EXPLICIT testing
 * affordance, not a privilege-escalation mechanism. So:
 *   - EXEC-LOCKED (stored 'executive', non-admin): a static "Executive" label. No
 *     switch — they cannot leave the executive view.
 *   - NON-ADMIN (pm or executive): a static role/persona indicator (a small label
 *     'PM' / 'Executive'). NO dropdown — persona is fixed by their stored role.
 *   - ADMIN: a 'View as:' dropdown labeled as a TESTING preview with a tooltip.
 *     When viewing as a non-admin persona, shows a subtle badge 'Viewing as PM'
 *     (or Executive) to make it clear they're in preview mode.
 *
 * isAdmin is the trusted fact from /api/me (the GRID_ATLAS_ADMINS allowlist), NOT
 * the self-selected persona. Server-side authz still keys off the real isAdmin.
 */
function PersonaSwitcher() {
  const { activePersona, setPersona, isAdmin, isExecLocked, isPreviewing, loading } = useRole()

  if (loading) return null

  // Exec-locked users get a static Executive label — they cannot switch.
  if (isExecLocked) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-navy-400">Role:</span>
        <span className="text-xs bg-navy-700 text-navy-200 border border-navy-600 rounded px-2 py-1">
          Executive
        </span>
      </div>
    )
  }

  // Non-admins: persona is inferred from their stored role. Show a STATIC label,
  // never a dropdown — they do not get to choose their persona.
  if (!isAdmin) {
    const label =
      activePersona === 'pm' ? 'PM' : activePersona === 'executive' ? 'Executive' : 'Admin'
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-navy-400">Role:</span>
        <span className="text-xs bg-navy-700 text-navy-200 border border-navy-600 rounded px-2 py-1">
          {label}
        </span>
      </div>
    )
  }

  // Admins: show a 'View as:' testing switcher. Make it clear this is a PREVIEW for
  // testing, not privilege escalation — server-side authz still keys off real isAdmin.
  //
  // The 'Admin' option is spread in ONLY when isAdmin is true, so a non-admin's
  // option list can never contain it — defense in depth on top of the !isAdmin
  // early-return above. PM and Executive are always offered (the personas an
  // admin can preview as).
  const personas: Array<{ value: Persona; label: string }> = [
    ...(isAdmin ? [{ value: 'admin' as Persona, label: 'Admin' }] : []),
    { value: 'pm', label: 'PM' },
    { value: 'executive', label: 'Executive' },
  ]

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <span
          className="text-xs text-navy-400"
          title="Preview the app as another persona — for testing. Your admin privileges are unchanged."
        >
          View as:
        </span>
        <select
          value={activePersona}
          onChange={(e) => setPersona(e.target.value as Persona)}
          className="text-xs bg-navy-700 text-navy-200 border border-navy-600 rounded px-2 py-1 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Preview the app as another persona — for testing. Your admin privileges are unchanged."
        >
          {personas.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </div>
      {/* Show a subtle badge when an admin is viewing as a non-admin persona */}
      {isPreviewing && (
        <span className="text-[10px] text-amber-400 bg-amber-900/20 border border-amber-700/30 rounded px-1.5 py-0.5">
          Viewing as {activePersona === 'pm' ? 'PM' : 'Executive'}
        </span>
      )}
    </div>
  )
}

/**
 * ROLES PHASE A — Get started button (onboarding entry point).
 *
 * MOVED out of the main horizontal nav row and into the top-right cluster, UNDER
 * the persona/role indicator, alongside the DEV/PROD pill. Onboarding is a
 * one-time action, not a recurring destination, so it does not belong among the
 * grouped nav destinations. The tab and its routing (setTab('onboarding')) are
 * unchanged — only the affordance's location moved.
 *
 * `visible` is driven by the persona's `entryTab` (admin/pm see it; executives do
 * not, per existing gating).
 */
function GetStartedButton({
  tab,
  setTab,
  visible,
}: {
  tab: TabId
  setTab: (id: TabId) => void
  visible: boolean
}) {
  if (!visible) return null

  const entry = TABS.find((t) => t.id === 'onboarding')
  if (!entry) return null

  const active = tab === 'onboarding'

  return (
    <button
      onClick={() => setTab('onboarding')}
      className={`text-xs flex items-center gap-1.5 px-3 py-1.5 rounded border transition-colors ${
        active
          ? 'bg-[#FF3621] text-white border-[#FF3621]'
          : 'bg-navy-700 text-navy-200 border-navy-600 hover:bg-navy-600'
      }`}
      title={entry.hint}
    >
      {entry.icon}
      {entry.label}
    </button>
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
  const { groups: NAV_GROUPS, entryTab } = filterNavForPersona(activePersona)
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
          {/* Top-right cluster: env pill, persona/role indicator, and — UNDER it —
              the Get started button (relocated out of the main nav). */}
          <div className="flex flex-col items-end gap-2">
            <div className="flex items-center gap-3">
              {env ? (
                <span
                  className={env.toUpperCase() === 'PROD' ? 'badge-high inline-flex items-center gap-1' : 'badge-muted inline-flex items-center gap-1'}
                  title="Deployment environment"
                >
                  <Activity className="w-3 h-3" />
                  {env.toUpperCase()}
                </span>
              ) : null}
              <PersonaSwitcher />
            </div>
            <GetStartedButton tab={tab} setTab={setTab} visible={entryTab !== null} />
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
        </nav>
      </div>
    </header>
  )
}
