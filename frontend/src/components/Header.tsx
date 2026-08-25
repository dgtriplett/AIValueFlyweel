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
 * Get Started button (onboarding entry point), rendered in the top-right cluster
 * alongside the env badge and persona indicator. Moved OUT of the main horizontal
 * nav since it's a one-time onboarding action, not a recurring destination.
 */
function GetStartedButton({ tab, setTab, visible }: { tab: TabId; setTab: (id: TabId) => void; visible: boolean }) {
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

/**
 * Persona switcher/indicator. For NON-admins: shows a static role label.
 * For admins: shows a switcher (full 'view as' UI comes in a later phase).
 *
 * Phase A: NON-admins see a static label (their persona is inferred from role, not chosen).
 * Admins keep a switcher for now.
 * Phase 4 (ADMIN LOCKDOWN): the 'admin' option is only OFFERED when the trusted
 *   isAdmin (from GET /api/me) is true.
 */
function PersonaSwitcher() {
  const { activePersona, setPersona, isAdmin, isExecLocked, loading } = useRole()

  if (loading) return null
  // Phase 2: hide for exec-locked users (they cannot switch anyway)
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

  // Phase A: non-admins see a static label (persona is inferred, not chosen)
  if (!isAdmin) {
    const label = activePersona === 'pm' ? 'PM' : activePersona === 'executive' ? 'Executive' : 'Admin'
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-navy-400">Role:</span>
        <span className="text-xs bg-navy-700 text-navy-200 border border-navy-600 rounded px-2 py-1">
          {label}
        </span>
      </div>
    )
  }

  // Phase 4: only OFFER 'admin' to a trusted admin. Non-admins see PM + Executive.
  const personas: Array<{ value: Persona; label: string }> = [
    ...(isAdmin ? [{ value: 'admin' as Persona, label: 'Admin' }] : []),
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
