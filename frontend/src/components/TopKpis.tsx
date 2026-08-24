// The persona-aware headline KPI strip at the top of the app.
//
// WHY THIS EXISTS
// ---------------
// The app used to render one fixed row of five StatCards for everyone — total use
// cases, data sources, shovel-ready, buildable value, realized value. That row was
// noise: an executive does not act on "data sources governed", and a PM reading the
// portfolio does not need the projected-annual-value headline number to do their
// day's triage. Everyone got everyone's numbers.
//
// The fix is to SHOW FEWER, MORE MEANINGFUL numbers per persona:
//   * executive — a minimal, value-focused set (realized, projected, going-live).
//     Big, clean, three cards. No operational clutter.
//   * pm — the operational set the PM acts on (total, in-progress, blocked,
//     shovel-ready). Four cards.
//   * admin — the PM (operational) set. Admins run the estate, so the operational
//     numbers are the ones they touch; kept to the same clean four.
//
// The SELECTION is a pure function (`kpisForPersona`) so it can be unit-tested
// directly against a portfolio snapshot without a DOM — see test/plumbing.test.ts.

import type { ReactNode } from 'react'
import { Layers, ShieldAlert, TrendingUp, Zap } from 'lucide-react'

import { fmtMoney } from '../constants'
import type { Persona } from '../context/RoleContext'
import type { UseCase } from '../types'
import { StatCard } from './StatCard'

/** One headline card, resolved to display-ready scalars by `kpisForPersona`. */
export interface Kpi {
  /** Stable key for React and for tests to assert the SET a persona gets. */
  key: string
  label: string
  /** Already formatted for display (money via fmtMoney, counts as strings). */
  value: string
  sub?: string
  accent: string
  /** Dims the sub-label — used for the "of $X potential" caveat. */
  muted?: boolean
}

/**
 * The portfolio facts the headline cards are derived from. Every field comes off
 * data ALREADY loaded in the app shell (the `/use-cases` list); nothing here needs
 * a new endpoint. This shape is what makes the selection unit-testable.
 */
export interface KpiData {
  /** Total use cases in the portfolio. */
  total: number
  /** status === 'in_progress'. */
  inProgress: number
  /** readiness === 'blocked' — the PM's at-risk pile. */
  blocked: number
  /** readiness === 'shovel_ready' — ready to start now. */
  shovelReady: number
  /** status live OR value_realized — use cases already in production. */
  live: number
  /** Sum of realized value ($M) — value banked to date. */
  realizedValue: number
  /** Sum of computed value on non-blocked use cases ($M) — projected annual value. */
  buildableValue: number
  /** Sum of computed value across ALL use cases ($M) — total potential. */
  totalValue: number
}

/**
 * Fold the app shell's already-loaded `/use-cases` list into the KPI facts.
 *
 * Kept apart from `kpisForPersona` so the reduction over the list happens once and
 * the persona selection stays a pure function of scalars, which is what the unit
 * test drives directly.
 */
export function kpiDataFromUseCases(useCases: UseCase[]): KpiData {
  let total = 0
  let inProgress = 0
  let blocked = 0
  let shovelReady = 0
  let live = 0
  let realizedValue = 0
  let buildableValue = 0
  let totalValue = 0

  for (const uc of useCases) {
    total += 1
    if (uc.status === 'in_progress') inProgress += 1
    if (uc.readiness === 'blocked') blocked += 1
    if (uc.readiness === 'shovel_ready') shovelReady += 1
    // "Live" is live + value_realized: both are in production, and an executive
    // reading "going live / already live" wants the production count, not just the
    // freshly-flipped ones.
    if (uc.status === 'live' || uc.status === 'value_realized') live += 1

    const value = uc.computed_value ?? 0
    totalValue += value
    // Buildable excludes blocked work — the projected number you can actually
    // deliver against, not the aspirational total. Mirrors App.tsx's own split.
    if (uc.readiness !== 'blocked') buildableValue += value
    realizedValue += uc.realized?.value ?? 0
  }

  return {
    total,
    inProgress,
    blocked,
    shovelReady,
    live,
    realizedValue,
    buildableValue,
    totalValue,
  }
}

/**
 * Select the headline KPI set for a persona — the linchpin of the reduction.
 *
 * The whole point is that the RETURNED SET DIFFERS by persona: an executive gets a
 * minimal value-focused three, a PM (and an admin) get the operational four. This
 * is a pure function of the persona and the derived scalars, so the test asserts
 * the exact set each persona sees without rendering anything.
 *
 * NOTE on "at-risk / going-live-soon": the `/use-cases` list rows do not carry the
 * per-use-case progression / target-go-live date (only the detail endpoint does),
 * so the executive "at-risk" signal is derived from the closest available field —
 * the count of blocked use cases — and the "going live" signal from the count
 * already live. Wiring a true target-date-based at-risk count would need the
 * progression data on the list payload.
 * TODO(kpi): surface progression.at_risk on the /use-cases list to show a
 * true "at risk of missing go-live" count rather than the blocked proxy.
 */
export function kpisForPersona(persona: Persona, data: KpiData): Kpi[] {
  if (persona === 'executive') {
    // Minimal, value-focused. Big, clean, few — no operational clutter.
    return [
      {
        key: 'realized_value',
        label: 'Value realized to date',
        value: fmtMoney(data.realizedValue),
        accent: '#42BA91',
      },
      {
        key: 'projected_value',
        label: 'Projected annual value',
        value: fmtMoney(data.buildableValue),
        sub: `of ${fmtMoney(data.totalValue)} total potential`,
        muted: true,
        accent: '#FFAB00',
      },
      {
        key: 'live',
        label: 'Use cases live',
        value: String(data.live),
        // Blocked doubles as the "needs attention" signal at the exec altitude.
        sub: data.blocked > 0 ? `${data.blocked} at risk / blocked` : undefined,
        accent: '#00A972',
      },
    ]
  }

  // pm AND admin: the operational set. Admin runs the estate, so it reads the same
  // numbers a PM acts on — kept to the same clean four rather than a longer row.
  return [
    {
      key: 'total',
      label: 'Use cases',
      value: String(data.total),
      accent: '#FF3621',
    },
    {
      key: 'in_progress',
      label: 'In progress',
      value: String(data.inProgress),
      accent: '#2272B4',
    },
    {
      key: 'blocked',
      label: 'Blocked / at-risk',
      value: String(data.blocked),
      accent: '#98102A',
    },
    {
      key: 'shovel_ready',
      label: 'Shovel-ready',
      value: String(data.shovelReady),
      accent: '#00A972',
    },
  ]
}

/** The icon each KPI renders with, keyed by its stable `key`. */
const KPI_ICONS: Record<string, ReactNode> = {
  realized_value: <TrendingUp className="w-5 h-5" />,
  projected_value: <TrendingUp className="w-5 h-5" />,
  live: <Zap className="w-5 h-5" />,
  total: <Layers className="w-5 h-5" />,
  in_progress: <TrendingUp className="w-5 h-5" />,
  blocked: <ShieldAlert className="w-5 h-5" />,
  shovel_ready: <Zap className="w-5 h-5" />,
}

export function TopKpis({ persona, useCases }: { persona: Persona; useCases: UseCase[] }) {
  const data = kpiDataFromUseCases(useCases)
  const kpis = kpisForPersona(persona, data)

  // A tighter grid than the old fixed five: executives get three big cards, the
  // operational set four — never the noisy five-wide row for everyone.
  const cols = kpis.length <= 3 ? 'md:grid-cols-3' : 'md:grid-cols-4'

  return (
    <div className={`grid grid-cols-2 ${cols} gap-4`}>
      {kpis.map((kpi) => (
        <StatCard
          key={kpi.key}
          icon={KPI_ICONS[kpi.key]}
          label={kpi.label}
          value={kpi.value}
          sub={kpi.sub}
          muted={kpi.muted}
          accent={kpi.accent}
        />
      ))}
    </div>
  )
}
