// At-Risk Rollup: the use cases that are slipping or overdue, and WHY.
//
// The Portfolio Manager and Executive personas both need one place that answers
// "what is not going to land on time, why, and how many times has it already
// moved?" — a question the per-use-case drawer can only answer one row at a time.
//
// Every figure here is computed server-side (GET /api/use-cases/at-risk) so the
// overdue math has one definition and the list is scoped to the caller's portfolio
// by the same visibility helper the rest of the use-case routes use. This view is
// read-only and routes ALL data through the account-scoped `http` client via
// `api.atRiskUseCases` — no raw fetch, no anchor hrefs.

import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'

import { api } from '../api'
import { QueryState } from '../components/Banner'
import { StatusBadge } from '../components/Badges'
import { StatStrip } from '../components/StatStrip'

/** ISO date -> short human date, or an em dash when no target is set. */
function formatDate(iso?: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export default function AtRiskView() {
  const atRisk = useQuery({ queryKey: ['at-risk'], queryFn: api.atRiskUseCases })

  const items = atRisk.data?.items ?? []
  const overdue = items.filter((item) => item.days_overdue > 0).length
  const slipped = items.filter((item) => item.times_slipped > 0).length
  const worst = items.reduce((max, item) => Math.max(max, item.days_overdue), 0)

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">At-risk use cases</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          Use cases whose target go-live has lapsed while they are not yet live, or that have
          slipped at least once. The reason is the latest recorded slippage note, and the count
          is how many times the target has moved later.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'At risk', value: items.length },
            { label: 'Overdue', value: overdue },
            { label: 'Has slipped', value: slipped },
            { label: 'Worst overdue', value: worst ? `${worst}d` : '—' },
          ]}
        />
      </div>

      <QueryState
        query={atRisk}
        loading="Finding what is slipping…"
        empty={!items.length}
        emptyMessage="Nothing is slipping or overdue right now — every use case with a target is on track."
      />

      {items.length ? (
        <div className="card p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[860px]">
              <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                <tr>
                  <th className="text-left px-3 py-2.5 font-medium">Use case</th>
                  <th className="text-left px-3 py-2.5 font-medium">Status</th>
                  <th className="text-left px-3 py-2.5 font-medium">Target date</th>
                  <th className="text-right px-3 py-2.5 font-medium">Days overdue</th>
                  <th className="text-right px-3 py-2.5 font-medium">Times slipped</th>
                  <th className="text-left px-3 py-2.5 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-navy-600 hover:bg-lava/5">
                    <td className="px-3 py-2.5 font-medium text-white">{item.title}</td>
                    <td className="px-3 py-2.5">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className="px-3 py-2.5 text-navy-300 font-mono text-xs">
                      {formatDate(item.target_go_live_date)}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono">
                      {item.days_overdue > 0 ? (
                        <span className="text-lava-300">{item.days_overdue}</span>
                      ) : (
                        <span className="text-navy-500">0</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {item.times_slipped}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-navy-400 max-w-[32ch]">
                      {item.latest_slippage_reason || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  )
}
