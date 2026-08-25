// The executive one-screen dashboard: value being driven + what's coming, at a glance.
//
// Ported from `console.js:2922-3040` (`viewExecutive`, `exportUseCaseTable`) and then
// recomposed as a single, presentation-ready screen for the Executive persona. The old
// pack dump (tables of buildable / blocked use cases, what-if candidates) is a working
// document for a PM, not the "value being driven, and what's next" read an executive wants
// the moment they land. So the pack DOWNLOAD stays — it is the deck-ready artifact — but the
// on-screen composition is now four cards:
//
//   1. Headline value metrics (from the executive pack's `metrics`).
//   2. The value-realization curve — WHEN projected value lands, by quarter
//      (GET /use-cases/portfolio/value-timeline, via `api.valueTimeline`).
//   3. An at-risk summary — how many are slipping, the worst few, WHY, with a link
//      through to the full At-Risk view (GET /use-cases/at-risk, via `api.atRiskUseCases`).
//   4. What's going live next — the nearest upcoming quarter off the same timeline.
//
// It invents no new backend endpoint: every number is composed from the executive-pack,
// value-timeline and at-risk endpoints that already exist. It is READ-ONLY — the only
// mutation on the screen is the pack download, which goes through `api.executivePackMarkdown()`
// and `saveBlob`, NOT an `<a download>` href. An anchor bypasses axios and therefore the
// account interceptor, the failure §4.1 of TIER3_MIGRATION_PLAN.md is about: the server falls
// back to the default account when the header is missing, so a customer would download somebody
// else's numbers with no error anywhere. All data routes through the account-scoped `http`
// client via the `api.*` methods.
//
// Endpoints: GET /exports/executive-pack, GET /exports/executive-pack.md,
//            GET /use-cases/portfolio/value-timeline, GET /use-cases/at-risk.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, Download, Rocket, TrendingUp } from 'lucide-react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatusBadge } from '../components/Badges'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast, useToast } from '../components/Toasts'
import { fmtMoney } from '../constants'
import { saveBlob } from '../lib/download'
import type { TabId } from '../components/Header'
import type { AtRiskUseCase, ValueTimelineBucket } from '../types'

const tickStyle = { fontSize: 11, fill: '#90A5B1' }

/** `#0d1f3c` is deliberately outside the Tailwind palette — recharts renders the
 *  tooltip into its own div, so this has to be an inline style object. Matches
 *  ValueTimelineView so the two value screens read as one family. */
const tooltipStyle = {
  background: '#0d1f3c',
  border: '1px solid #2A4A56',
  borderRadius: 8,
  color: '#fff',
  fontSize: 12,
}

/** `2026-08-22 14:05:11` from the pack's ISO timestamp. Sliced, not reparsed:
 *  the server means UTC and says so in the caption. */
function generatedAt(iso?: string | null): string {
  return (iso ?? '').slice(0, 19).replace('T', ' ')
}

/**
 * The next quarter with value still to land, relative to now.
 *
 * The timeline series is chronological (server-bucketed), so "what's next" is the
 * first bucket whose quarter is >= the current one and that carries value. We
 * compare quarter labels (`YYYY-Qn`) lexicographically, which is correct because
 * the format sorts the same as time.
 */
function nextQuarter(series: ValueTimelineBucket[]): ValueTimelineBucket | null {
  if (!series.length) return null
  const now = new Date()
  const q = Math.floor(now.getMonth() / 3) + 1
  const current = `${now.getFullYear()}-Q${q}`
  const upcoming = series.find((b) => b.quarter >= current && b.value_landing > 0)
  // Nothing scheduled from here on — fall back to the earliest bucket that carries
  // value, so the card is never empty when a series with value exists.
  return upcoming ?? series.find((b) => b.value_landing > 0) ?? null
}

export default function ExecutiveView({ setTab }: { setTab?: (tab: TabId) => void }) {
  const queryClient = useQueryClient()
  const { show } = useToast()
  const reportError = useApiErrorToast()

  const pack = useQuery({ queryKey: ['executive-pack'], queryFn: api.executivePack })
  const timeline = useQuery({ queryKey: ['value-timeline'], queryFn: api.valueTimeline })
  const atRisk = useQuery({ queryKey: ['at-risk'], queryFn: api.atRiskUseCases })

  const download = useMutation({
    mutationFn: api.executivePackMarkdown,
    onSuccess: (blob) => {
      saveBlob(blob, 'grid-atlas-executive-pack.md')
      show({ kind: 'info', message: 'Executive pack downloaded.' })
    },
    onError: (error) => reportError(error, 'Could not build the pack.'),
  })

  const data = pack.data
  const metrics = data?.metrics ?? {}

  const series = timeline.data?.series ?? []
  const unscheduled = timeline.data?.unscheduled ?? { use_case_count: 0, value_landing: 0 }
  const totalCumulative = series.length ? series[series.length - 1].cumulative_value : 0
  const upcoming = nextQuarter(series)

  const atRiskItems: AtRiskUseCase[] = atRisk.data?.items ?? []
  const overdue = atRiskItems.filter((item) => item.days_overdue > 0).length
  // Worst first: overdue leaders, then the ones that have slipped the most times.
  const topAtRisk = [...atRiskItems]
    .sort((a, b) => b.days_overdue - a.days_overdue || b.times_slipped - a.times_slipped)
    .slice(0, 3)

  const refreshing = pack.isFetching || timeline.isFetching || atRisk.isFetching

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-lava" />
            <h2 className="font-bold text-white">Executive dashboard</h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn-primary text-sm"
              disabled={download.isPending}
              onClick={() => download.mutate()}
            >
              <Download className="w-4 h-4" />
              {download.isPending ? 'Preparing…' : 'Download pack'}
            </button>
            <button
              className="btn-secondary text-sm"
              disabled={refreshing}
              // Invalidation, not the console's `refresh: viewExecutive` re-render.
              // Each figure is derived from the whole portfolio, so "refresh" means
              // "drop the cache entries", and react-query owns that.
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ['executive-pack'] })
                queryClient.invalidateQueries({ queryKey: ['value-timeline'] })
                queryClient.invalidateQueries({ queryKey: ['at-risk'] })
              }}
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          {(data?.company?.company_name ?? 'The portfolio') +
            ' at a glance: the value being driven, when it lands, and what is at risk.'}
          {data?.generated_at ? ` Generated ${generatedAt(data.generated_at)} UTC.` : ''}
        </p>
      </div>

      <QueryState query={pack} loading="Building the executive read…" />

      {/* Headline value metrics — the numbers an executive reads first. */}
      {data ? (
        <div className="card">
          <StatStrip
            stats={[
              { label: 'Total value', value: `$${metrics.total_value_mm ?? 0}M` },
              { label: 'Shovel-ready', value: `$${metrics.buildable_value_mm ?? 0}M` },
              { label: 'Realized', value: `$${metrics.realized_value_mm ?? 0}M` },
              { label: 'Use cases', value: metrics.use_cases_total ?? '—' },
              { label: 'Blocked', value: metrics.blocked ?? '—', lowerIsBetter: true },
            ]}
          />
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Value-realization curve — spans two columns; the tallest, headline panel. */}
        <div className="card lg:col-span-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-[#00A972]" />
              <h3 className="text-sm font-semibold text-white">Value realization by quarter</h3>
            </div>
            {series.length && setTab ? (
              <button
                className="text-xs text-navy-400 hover:text-white inline-flex items-center gap-1"
                onClick={() => setTab('timeline')}
              >
                Full timeline <ArrowRight className="w-3 h-3" />
              </button>
            ) : null}
          </div>
          {series.length ? (
            <>
              <div className="mt-3">
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={series} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
                    <XAxis dataKey="quarter" tick={tickStyle} interval={0} />
                    <YAxis tick={tickStyle} tickFormatter={(value) => `$${value}M`} />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number, name) => [
                        `$${Number(value).toFixed(1)}M`,
                        name === 'cumulative_value' ? 'Cumulative' : 'Landing this quarter',
                      ]}
                    />
                    <Bar dataKey="value_landing" fill="#00A972" radius={[4, 4, 0, 0]} />
                    <Line
                      type="monotone"
                      dataKey="cumulative_value"
                      stroke="#FF3621"
                      strokeWidth={2}
                      dot={{ r: 3 }}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="text-xs text-navy-500 mt-2">
                {`Cumulative projected value of ${fmtMoney(totalCumulative)} by end of the series.`}
                {unscheduled.use_case_count > 0
                  ? ` ${unscheduled.use_case_count} use case${
                      unscheduled.use_case_count === 1 ? '' : 's'
                    } off the curve (no target date), carrying ${fmtMoney(
                      unscheduled.value_landing,
                    )}.`
                  : ''}
              </p>
            </>
          ) : (
            <div className="mt-3">
              <Banner kind="info">
                No use cases have a target go-live date yet — set target dates to see when value is
                expected to land.
              </Banner>
            </div>
          )}
        </div>

        {/* At-risk summary — the compact "what's slipping" read, links to the full view. */}
        <div className="card flex flex-col">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-lava" />
              <h3 className="text-sm font-semibold text-white">At risk</h3>
            </div>
            {atRiskItems.length && setTab ? (
              <button
                className="text-xs text-navy-400 hover:text-white inline-flex items-center gap-1"
                onClick={() => setTab('atrisk')}
              >
                View all <ArrowRight className="w-3 h-3" />
              </button>
            ) : null}
          </div>
          <div className="mt-3">
            <StatStrip
              stats={[
                { label: 'At risk', value: atRiskItems.length, lowerIsBetter: true },
                { label: 'Overdue', value: overdue, lowerIsBetter: true },
              ]}
            />
          </div>
          {topAtRisk.length ? (
            <ul className="mt-4 space-y-3">
              {topAtRisk.map((item) => (
                <li key={item.id} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-white truncate">{item.title}</span>
                    <StatusBadge status={item.status} />
                  </div>
                  <div className="text-xs text-navy-400 mt-0.5">
                    {item.days_overdue > 0 ? (
                      <span className="text-lava-300">{`${item.days_overdue}d overdue`}</span>
                    ) : (
                      <span>{`slipped ${item.times_slipped}×`}</span>
                    )}
                    {item.latest_slippage_reason ? ` — ${item.latest_slippage_reason}` : ''}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-4">
              <Banner kind="ok">Nothing is slipping or overdue right now.</Banner>
            </div>
          )}
        </div>
      </div>

      {/* What's going live next — the nearest upcoming quarter off the same timeline. */}
      <div className="card">
        <div className="flex items-center gap-2">
          <Rocket className="w-4 h-4 text-[#00A972]" />
          <h3 className="text-sm font-semibold text-white">What's going live next</h3>
        </div>
        {upcoming ? (
          <p className="text-sm text-navy-300 mt-2 max-w-[70ch]">
            <span className="font-mono text-white">{upcoming.quarter}</span>
            {`: ${upcoming.use_case_count} use case${
              upcoming.use_case_count === 1 ? '' : 's'
            } expected to go live, landing `}
            <span className="text-[#00A972] font-medium">{fmtMoney(upcoming.value_landing)}</span>
            {' of projected annual value.'}
          </p>
        ) : (
          <p className="text-sm text-navy-400 mt-2 max-w-[70ch]">
            Nothing scheduled to go live yet — set target go-live dates on use cases to project the
            next wave of value.
          </p>
        )}
      </div>
    </div>
  )
}
