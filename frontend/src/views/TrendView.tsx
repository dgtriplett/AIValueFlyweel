// Trend: how the portfolio has actually moved.
//
// Ported from `console.js:3254-3424` (`viewTrend`, `sparkline`, `renderTrendTable`).
// Each point is a real event — assumptions calibrated, a source landed, a sync
// that changed something — so the chart explains itself rather than needing a
// changelog beside it.
//
// WHAT THIS PORT LEAVES OUT, AND WHY
// ----------------------------------
// The console's Trend view mixed two writes into an otherwise read-only screen:
//
//   1. "Snapshot now"  → POST   /snapshots        (`console.js:3270-3276`)
//   2. "Remove" a point → DELETE /snapshots/{id}  (`console.js:3326-3334`)
//
// Both are `limiter("write")` server-side (`server/routes/snapshots.py`) and this
// phase is read-only by contract, so neither is ported. Nor should they be ported
// casually: the delete is behind a `confirm()` for a real reason the console
// spells out — "a snapshot taken mid-migration is misleading and cannot be
// corrected, the state it recorded is gone" — so it needs the SPA's confirm
// discipline rather than a bare `window.confirm`.
//
// TODO(Tier 3): the two snapshot writes above still need a home. §4.8's phase
// table does not assign them — Trend appears only under Phase 3 (read-only) — so
// they fall through the sequence. They belong with the curation writes in Phase 5
// (`limiter("write")`, no confirm token), with the delete gated on a real confirm
// dialog rather than `window.confirm`. Losing "Snapshot now" is not a coverage
// gap in the meantime: `snapshots.py` captures automatically when something moves
// the portfolio, and the read-only "What would it say?" below covers the "before
// I commit, what does it look like" question the manual button was used for.
//
// Endpoints: GET /snapshots?limit=500, GET /snapshots/current. Both unlimited reads.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LineChart as LineChartIcon } from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import type { Snapshot, SnapshotMetrics } from '../types'

const tickStyle = { fontSize: 11, fill: '#90A5B1' }

/** recharts renders its tooltip into its own div, so this has to be inline. */
const tooltipStyle = {
  background: '#0d1f3c',
  border: '1px solid #2A4A56',
  borderRadius: 8,
  color: '#fff',
  fontSize: 12,
}

/** `2026-08-22 14:05` from an ISO string, without pulling in a date library.
 *  Sliced rather than parsed deliberately: the server sends UTC and a local-time
 *  reparse would silently shift every point in the table by the reader's offset,
 *  which is how two people comparing the same trend end up disagreeing. */
function stamp(iso?: string | null, length = 16): string {
  return (iso ?? '').slice(0, length).replace('T', ' ')
}

export default function TrendView() {
  const [current, setCurrent] = useState<SnapshotMetrics | null>(null)

  const trend = useQuery({ queryKey: ['snapshots'], queryFn: () => api.snapshots(500) })
  // Deliberately not enabled on mount: it is the answer to a question the user
  // asks by clicking, and computing it costs a full portfolio revaluation.
  const now = useQuery({
    queryKey: ['snapshot-current'],
    queryFn: api.currentSnapshot,
    enabled: false,
  })

  const series = trend.data?.snapshots ?? []
  const change = trend.data?.change_since_first ?? {}
  const latest = trend.data?.latest

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <LineChartIcon className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Trend</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          How the portfolio has moved. Each point is a real event — assumptions calibrated, a
          source landed, a sync that changed something — so the chart explains itself rather than
          needing a changelog. New points are captured automatically when something moves the
          portfolio.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <button
          className="btn-secondary text-sm"
          disabled={now.isFetching}
          onClick={() => {
            void now.refetch().then((result) => {
              if (result.data) setCurrent(result.data.metrics)
            })
          }}
        >
          {now.isFetching ? 'Computing…' : 'What would it say?'}
        </button>
        <span className="text-xs text-navy-500">
          Computes the current metrics without storing a point.
        </span>
      </div>

      {current ? (
        <div className="card">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-navy-500 mb-3">
            Right now — not stored
          </h3>
          <StatStrip
            stats={[
              { label: 'Total value', value: `$${current.total_value_mm ?? 0}M` },
              { label: 'Buildable', value: `$${current.buildable_value_mm ?? 0}M` },
              { label: 'Shovel-ready', value: current.shovel_ready ?? '—' },
              {
                label: 'Sources',
                value: `${current.sources_ready ?? '—'} / ${current.sources_total ?? '—'}`,
              },
              {
                label: 'Domains met',
                value: `${current.domains_satisfied ?? '—'} / ${current.domains_total ?? '—'}`,
              },
            ]}
          />
        </div>
      ) : null}

      <QueryState
        query={trend}
        loading="Loading snapshots…"
        empty={series.length === 0}
        emptyMessage={trend.data?.note ?? 'No snapshots yet.'}
      />

      {/* One point is not a trend, but it is worth showing: the table below tells
          you what was recorded and when the baseline was set. */}
      {series.length === 1 ? (
        <Banner kind="info">
          One snapshot so far — a trend needs at least two points. The next one is captured
          automatically when something moves the portfolio.
        </Banner>
      ) : null}

      {series.length > 1 ? (
        <>
          <div className="card">
            <StatStrip
              stats={[
                {
                  label: 'Buildable value',
                  value: `$${latest?.buildable_value_mm ?? 0}M`,
                  change: change.buildable_value_mm,
                },
                {
                  label: 'Shovel-ready',
                  value: latest?.shovel_ready ?? '—',
                  change: change.shovel_ready,
                },
                {
                  label: 'Sources landed',
                  value: latest?.sources_ready ?? '—',
                  change: change.sources_ready,
                },
                {
                  label: 'Domains met',
                  value: latest?.domains_satisfied ?? '—',
                  change: change.domains_satisfied,
                },
                {
                  label: 'Blocked',
                  value: latest?.blocked ?? '—',
                  change: change.blocked,
                  lowerIsBetter: true,
                },
              ]}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <TrendChart
              series={series}
              field="buildable_value_mm"
              title="Buildable value ($M)"
              format={(value) => `$${value}M`}
              color="#FF3621"
            />
            <TrendChart
              series={series}
              field="shovel_ready"
              title="Shovel-ready use cases"
              format={(value) => String(value)}
              color="#00A972"
            />
          </div>
        </>
      ) : null}

      {series.length ? (
        <div className="card p-0">
          <h3 className="text-sm font-semibold text-white px-4 pt-4 pb-2">Every point</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[720px]">
              <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                <tr>
                  <th className="text-left px-3 py-2.5 font-medium">When</th>
                  <th className="text-left px-3 py-2.5 font-medium">Why</th>
                  <th className="text-right px-3 py-2.5 font-medium">Buildable</th>
                  <th className="text-right px-3 py-2.5 font-medium">Shovel-ready</th>
                  <th className="text-right px-3 py-2.5 font-medium">Sources</th>
                </tr>
              </thead>
              <tbody>
                {/* Newest first: the chart reads left-to-right chronologically, but
                    a list is scanned from the top and the recent events are the
                    interesting ones. `toReversed` is ES2023, so this is a copy. */}
                {[...series].reverse().map((snapshot) => (
                  <tr key={snapshot.id} className="border-b border-navy-600 hover:bg-lava/5">
                    <td className="px-3 py-2.5 text-xs font-mono text-navy-300">
                      {stamp(snapshot.captured_at)}
                    </td>
                    <td className="px-3 py-2.5 text-xs">
                      <div className="text-navy-300">
                        {(snapshot.reason ?? '').replace(/_/g, ' ')}
                      </div>
                      {snapshot.detail ? (
                        <div className="text-navy-500">{snapshot.detail}</div>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {`$${snapshot.buildable_value_mm ?? 0}M`}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {snapshot.shovel_ready ?? '—'}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {snapshot.sources_ready ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-navy-500 px-4 py-3">
            {`${series.length} point(s) since ${stamp(trend.data?.first_captured_at, 10)}.`}
          </p>
        </div>
      ) : null}
    </div>
  )
}

/**
 * One metric over the snapshot series.
 *
 * Points are spaced by INDEX, not by timestamp — the console's `sparkline`
 * comment explains why and it still holds: snapshots are event-triggered, so real
 * time between them is arbitrary (three in one afternoon, then nothing for a
 * month). Spacing by time squashes the interesting cluster into a few pixels;
 * spacing by event makes each change equally readable, and the table above
 * carries the actual dates. recharts gets a categorical X axis to enforce that,
 * rather than a numeric time axis.
 */
function TrendChart({
  series,
  field,
  title,
  format,
  color,
}: {
  series: Snapshot[]
  field: 'buildable_value_mm' | 'shovel_ready'
  title: string
  format: (value: number) => string
  color: string
}) {
  const data = series.map((snapshot) => ({
    when: stamp(snapshot.captured_at, 10),
    value: Number(snapshot[field] ?? 0),
  }))

  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-white mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
          <XAxis dataKey="when" tick={tickStyle} />
          <YAxis tick={tickStyle} tickFormatter={format} />
          <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => [format(value), title]} />
          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            dot={{ fill: color, r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
