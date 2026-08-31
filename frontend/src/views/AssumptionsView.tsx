// The shared value model, exposed as editable numbers.
//
// Every hypothesized and calculated-realized figure in the app is a formula over
// these assumptions, so one edit here re-quantifies the whole portfolio. That is
// why a save invalidates the use-case list and the blast radius too, not just the
// two totals shown at the top of this view. The exact set is owned by
// `useAssumptionInvalidation`, shared with the research recalibration path so a
// manual edit and a recalibration refresh identical KPIs.

import { useMutation, useQuery } from '@tanstack/react-query'
import { RotateCcw, SlidersVertical, TrendingUp } from 'lucide-react'
import { useState } from 'react'

import { api } from '../api'
import { fmtMoney } from '../constants'
import { useAssumptionInvalidation } from '../hooks/useAssumptionInvalidation'
import type { Assumption } from '../types'

export default function AssumptionsView() {
  const assumptionsQuery = useQuery({ queryKey: ['assumptions'], queryFn: api.assumptions })
  const portfolioQuery = useQuery({ queryKey: ['portfolio-value'], queryFn: api.portfolioValue })
  const [drafts, setDrafts] = useState<Record<string, number>>({})

  // A manual edit and a research recalibration re-quantify the SAME KPIs, so both
  // invalidate through this one hook. See `hooks/useAssumptionInvalidation.ts`.
  const invalidateKpis = useAssumptionInvalidation()

  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number }) =>
      api.updateAssumption(key, value),
    onSuccess: invalidateKpis,
  })

  const assumptions = assumptionsQuery.data ?? []
  const categories = [...new Set(assumptions.map((a) => a.category ?? 'Other'))]
  const hypothesized = portfolioQuery.data?.total_hypothesized_value
  const realized = portfolioQuery.data?.total_realized_value

  // Only write when the number actually moved: blur fires on every tab-through,
  // and a no-op PUT would invalidate four queries for nothing.
  const commit = (assumption: Assumption) => {
    const draft = drafts[assumption.key]
    if (draft != null && draft !== assumption.value) {
      save.mutate({ key: assumption.key, value: draft })
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card border-l-4 border-l-lava flex items-center gap-4">
          <TrendingUp className="w-8 h-8 text-lava" />
          <div>
            <div className="text-2xl font-bold text-white">{fmtMoney(hypothesized)}</div>
            <div className="text-xs text-navy-400 uppercase tracking-wide">
              Portfolio hypothesized value / yr
            </div>
          </div>
        </div>
        <div className="card border-l-4 border-l-success flex items-center gap-4">
          <TrendingUp className="w-8 h-8 text-success" />
          <div>
            <div className="text-2xl font-bold text-white">{fmtMoney(realized)}</div>
            <div className="text-xs text-navy-400 uppercase tracking-wide">
              Portfolio realized value / yr
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <SlidersVertical className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-lg">Value Assumptions</h2>
        </div>
        <p className="text-sm text-navy-400 mb-4">
          Adjust these to your organization. Every use case's hypothesized and
          calculated-realized value re-quantifies instantly across the portfolio and the blast
          radius.
        </p>

        {assumptionsQuery.isLoading && (
          <div className="text-sm text-navy-400">Loading assumptions…</div>
        )}
        {assumptionsQuery.isError && (
          <div className="text-sm text-lava-300">Couldn't load assumptions. Please retry.</div>
        )}
        {!assumptionsQuery.isLoading && !assumptionsQuery.isError && assumptions.length === 0 && (
          <div className="text-sm text-navy-500">No assumptions configured yet.</div>
        )}

        {categories.map((category) => (
          <div key={category} className="mb-5">
            <div className="text-xs font-semibold uppercase tracking-wide text-navy-500 mb-2 border-b border-navy-600 pb-1">
              {category}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {assumptions
                .filter((a) => (a.category ?? 'Other') === category)
                .map((assumption) => (
                  <div
                    key={assumption.key}
                    className="flex items-center justify-between gap-3"
                  >
                    <label
                      htmlFor={`assumption-${assumption.key}`}
                      className="text-sm text-navy-300 flex-1"
                      title={assumption.key}
                    >
                      {assumption.label}
                    </label>
                    <div className="flex items-center gap-1">
                      <input
                        id={`assumption-${assumption.key}`}
                        name={`assumption-${assumption.key}`}
                        aria-label={assumption.label ?? assumption.key}
                        type="number"
                        className="input-field w-28 text-right"
                        value={drafts[assumption.key] ?? assumption.value}
                        onChange={(event) =>
                          setDrafts((previous) => ({
                            ...previous,
                            [assumption.key]: Number(event.target.value),
                          }))
                        }
                        onBlur={() => commit(assumption)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') commit(assumption)
                        }}
                      />
                      <span className="text-xs text-navy-500 w-14">{assumption.unit}</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ))}

        <div className="text-xs text-navy-500 flex items-center gap-1">
          <RotateCcw className="w-3 h-3" /> Edits persist immediately (blur or Enter) and
          recompute portfolio value.
        </div>
      </div>
    </div>
  )
}
