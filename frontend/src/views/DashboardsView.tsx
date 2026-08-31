// The analytics view: four KPI cards, four charts, a readiness heatmap and a
// realized-value trend.
//
// Every chart reads off one `/analytics/dashboard` payload — the server does the
// aggregation so the numbers here always agree with the ones the portfolio table
// shows. Nothing is fetched per-chart.

import { useQuery } from '@tanstack/react-query'
import { TrendingUp } from 'lucide-react'
import type { ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../api'
import { StatCard } from '../components/StatCard'
import {
  LOBS,
  PHASE_LABELS,
  READINESS_COLORS,
  STATUS_COLORS,
  STATUS_LABELS,
  fmtMoney,
} from '../constants'
import type { Readiness } from '../types'

const tickStyle = { fontSize: 11, fill: '#90A5B1' }

/** `#0d1f3c` is deliberately outside the Tailwind palette — recharts renders the
 *  tooltip into its own div, so this has to be an inline style object. */
const tooltipStyle = {
  background: '#0d1f3c',
  border: '1px solid #2A4A56',
  borderRadius: 8,
  color: '#fff',
  fontSize: 12,
}

const READINESS_BREAKDOWN: Readiness[] = ['shovel_ready', 'nearly_ready', 'blocked']

// Phase is internal — it is derived from prerequisite depth and is never offered
// as a customer-facing filter. The heatmap keeps it because "which domains have
// work at which depth" is the one question it answers well.
const HEATMAP_PHASES = [1, 2, 3]

export default function DashboardsView() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['analytics-dashboard'],
    queryFn: api.dashboard,
  })

  if (isError)
    return <div className="text-lava-300 text-sm">Couldn't load dashboards. Please retry.</div>
  if (isLoading || !data) return <div className="text-navy-400">Loading dashboards…</div>

  const totals = data.totals
  const byStatus = data.waterfall_by_status.map((row) => ({
    name: STATUS_LABELS[row.status] ?? row.status,
    value: row.hyp_mm,
    status: row.status,
  }))
  const lobCoverage = data.lob_coverage
  const utilization = data.utilization

  const counts: Record<string, Record<number, number>> = {}
  let maxCount = 1
  for (const cell of data.heatmap) {
    counts[cell.lob] = counts[cell.lob] || {}
    counts[cell.lob][cell.phase] = cell.count
    if (cell.count > maxCount) maxCount = cell.count
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Buildable value / yr"
          value={fmtMoney(totals.hypothesized_buildable_mm ?? totals.hypothesized_mm)}
          sub={`of ${fmtMoney(totals.hypothesized_mm)} total potential`}
          accent="#FFAB00"
          icon={<TrendingUp className="w-7 h-7" />}
        />
        <StatCard
          label="Realized value / yr"
          value={fmtMoney(totals.realized_mm)}
          accent="#00A972"
          icon={<TrendingUp className="w-7 h-7" />}
        />
        <StatCard
          label="Value capture"
          value={`${totals.capture_pct}%`}
          sub="realized ÷ hypothesized"
          accent="#2272B4"
          icon={<TrendingUp className="w-7 h-7" />}
        />
        <StatCard
          label="Live use cases"
          value={String((data.status_dist.live ?? 0) + (data.status_dist.value_realized ?? 0))}
          accent="#FF3621"
          icon={<TrendingUp className="w-7 h-7" />}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Hypothesized value by project status">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={byStatus} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
              <XAxis
                dataKey="name"
                tick={tickStyle}
                interval={0}
                angle={-15}
                textAnchor="end"
                height={50}
              />
              <YAxis tick={tickStyle} tickFormatter={(value) => `$${value}M`} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value) => [`$${value}M`, 'Hypothesized']}
              />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {byStatus.map((row) => (
                  <Cell key={row.status} fill={STATUS_COLORS[row.status] ?? '#618794'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Value by domain (LOB)">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart
              data={lobCoverage}
              layout="vertical"
              margin={{ top: 8, right: 16, bottom: 8, left: 10 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
              <XAxis type="number" tick={tickStyle} tickFormatter={(value) => `$${value}M`} />
              <YAxis type="category" dataKey="lob" tick={tickStyle} width={110} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name) => [
                  `$${value}M`,
                  name === 'hyp' ? 'Hypothesized' : 'Realized',
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="hyp" name="Hypothesized" fill="#FFAB00" radius={[0, 4, 4, 0]} />
              <Bar dataKey="real" name="Realized" fill="#00A972" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Readiness heatmap — domain × phase (use-case count)">
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr>
                  <th className="text-left p-1 text-navy-400" />
                  {HEATMAP_PHASES.map((phase) => (
                    <th key={phase} className="p-1 text-navy-400 font-medium">
                      P{phase}
                      <div className="text-[9px] text-navy-500">
                        {PHASE_LABELS[phase].split(' ')[0]}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {LOBS.map((lob) => (
                  <tr key={lob}>
                    <td className="p-1 text-navy-300 whitespace-nowrap">{lob}</td>
                    {HEATMAP_PHASES.map((phase) => {
                      const count = counts[lob]?.[phase] ?? 0
                      // Floor of .05 keeps an empty cell visible as a cell rather
                      // than a hole in the grid.
                      const alpha = count === 0 ? 0.05 : 0.2 + 0.8 * (count / maxCount)
                      return (
                        <td
                          key={phase}
                          className="p-1 text-center font-semibold"
                          style={{
                            background: `rgba(255,54,33,${alpha})`,
                            color: count ? '#fff' : '#618794',
                          }}
                        >
                          {count || '·'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ChartCard>

        <ChartCard title="Top data-asset utilization (use cases requiring)">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart
              data={utilization.slice(0, 10)}
              layout="vertical"
              margin={{ top: 8, right: 16, bottom: 8, left: 10 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
              <XAxis type="number" tick={tickStyle} />
              <YAxis
                type="category"
                dataKey="label"
                tick={{ ...tickStyle, fontSize: 9 }}
                width={150}
              />
              <Tooltip contentStyle={tooltipStyle} formatter={(value) => [value, 'use cases']} />
              <Bar dataKey="uc_count" fill="#2272B4" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Portfolio readiness">
          <div className="flex items-center gap-6 py-6 justify-center">
            {READINESS_BREAKDOWN.map((readiness) => (
              <div key={readiness} className="text-center">
                <div className="text-3xl font-bold" style={{ color: READINESS_COLORS[readiness] }}>
                  {data.readiness_dist[readiness] ?? 0}
                </div>
                <div className="text-xs text-navy-400 mt-1">{readiness.replace('_', ' ')}</div>
              </div>
            ))}
          </div>
          {/* flexGrow, not a percentage width: the three segments then divide the
              bar proportionally without anyone computing a total. */}
          <div className="flex h-3 rounded-full overflow-hidden mt-2">
            {READINESS_BREAKDOWN.map((readiness) => (
              <div
                key={readiness}
                style={{
                  background: READINESS_COLORS[readiness],
                  flexGrow: data.readiness_dist[readiness] ?? 0,
                }}
              />
            ))}
          </div>
        </ChartCard>

        <ChartCard title="Realized value by fiscal period">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart
              data={data.realized_by_period}
              margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1B3139" />
              <XAxis dataKey="period" tick={tickStyle} />
              <YAxis
                tick={tickStyle}
                tickFormatter={(value: number) => `$${(value / 1e6).toFixed(0)}M`}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value: number) => [`$${(value / 1e6).toFixed(1)}M`, 'Realized']}
              />
              <Line
                type="monotone"
                dataKey="amount"
                stroke="#00A972"
                strokeWidth={2}
                dot={{ fill: '#00A972' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-white mb-3">{title}</h3>
      {children}
    </div>
  )
}
