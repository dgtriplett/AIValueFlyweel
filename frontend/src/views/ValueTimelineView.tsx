// Value-Realization Timeline: WHEN projected value is expected to land.
//
// The Executive comes to this view to answer one question — "what value is coming,
// and when?" — that no other screen answers on a time axis. The portfolio's
// projected annual value is bucketed by the QUARTER of each use case's target
// go-live date (from the account-scoped progression tables), summed per quarter,
// and threaded with a running cumulative so the curve reads as "how much value is
// live/expected by end of each quarter". Use cases with no target date have no
// quarter to plot, so they are reported separately as an "unscheduled" bucket.
//
// Every figure is computed server-side (GET /api/use-cases/portfolio/value-timeline)
// so the bucketing has one definition and the series is scoped to the caller's
// portfolio by the same visibility helper the rest of the use-case routes use. This
// view is read-only and routes ALL data through the account-scoped `http` client via
// `api.valueTimeline` — no raw fetch.

import { useQuery } from '@tanstack/react-query'
import { TrendingUp } from 'lucide-react'
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
import { QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { fmtMoney } from '../constants'

const tickStyle = { fontSize: 11, fill: '#90A5B1' }

/** `#0d1f3c` is deliberately outside the Tailwind palette — recharts renders the
 *  tooltip into its own div, so this has to be an inline style object. Matches
 *  DashboardsView so the two value screens read as one family. */
const tooltipStyle = {
  background: '#0d1f3c',
  border: '1px solid #2A4A56',
  borderRadius: 8,
  color: '#fff',
  fontSize: 12,
}

export default function ValueTimelineView() {
  const timeline = useQuery({ queryKey: ['value-timeline'], queryFn: api.valueTimeline })

  const series = timeline.data?.series ?? []
  const unscheduled = timeline.data?.unscheduled ?? { use_case_count: 0, value_landing: 0 }

  const scheduledCount = series.reduce((sum, b) => sum + b.use_case_count, 0)
  const scheduledValue = series.reduce((sum, b) => sum + b.value_landing, 0)
  const totalCumulative = series.length ? series[series.length - 1].cumulative_value : 0

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-[#00A972]">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-[#00A972]" />
          <h2 className="font-bold text-white">Value-realization timeline</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          When the portfolio's projected annual value is expected to land, bucketed by the
          quarter of each use case's target go-live date. Bars are the value landing in each
          quarter; the line is the running cumulative. Value is realized where recorded, else
          hypothesized. Use cases with no target date are counted separately, below.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Scheduled use cases', value: scheduledCount },
            { label: 'Scheduled value', value: fmtMoney(scheduledValue) },
            { label: 'Cumulative by end', value: fmtMoney(totalCumulative) },
            { label: 'Unscheduled', value: unscheduled.use_case_count },
          ]}
        />
      </div>

      <QueryState
        query={timeline}
        loading="Projecting when value lands…"
        empty={!series.length}
        emptyMessage="No use cases have a target go-live date yet — set target dates to see when value is expected to land."
      />

      {series.length ? (
        <>
          <div className="card">
            <h3 className="text-sm font-semibold text-white mb-3">Value landing by quarter</h3>
            <ResponsiveContainer width="100%" height={300}>
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

          <div className="card p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[560px]">
                <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                  <tr>
                    <th className="text-left px-3 py-2.5 font-medium">Quarter</th>
                    <th className="text-right px-3 py-2.5 font-medium">Use cases</th>
                    <th className="text-right px-3 py-2.5 font-medium">Value landing</th>
                    <th className="text-right px-3 py-2.5 font-medium">Cumulative value</th>
                  </tr>
                </thead>
                <tbody>
                  {series.map((bucket) => (
                    <tr key={bucket.quarter} className="border-b border-navy-600 hover:bg-white/5">
                      <td className="px-3 py-2.5 font-medium text-white font-mono text-xs">
                        {bucket.quarter}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                        {bucket.use_case_count}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-[#00A972]">
                        {fmtMoney(bucket.value_landing)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-white">
                        {fmtMoney(bucket.cumulative_value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : null}

      {unscheduled.use_case_count > 0 ? (
        <div className="card border-l-4 border-l-navy-500">
          <h3 className="text-sm font-semibold text-white">Unscheduled</h3>
          <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
            {unscheduled.use_case_count} use case{unscheduled.use_case_count === 1 ? '' : 's'} with
            no target go-live date, carrying {fmtMoney(unscheduled.value_landing)} of projected
            value. These are off the curve until a target date is set.
          </p>
        </div>
      ) : null}
    </div>
  )
}
