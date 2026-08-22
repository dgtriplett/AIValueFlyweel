// What-if: pick unlanded sources, see what landing them would unblock.
//
// Ported from `console.js:2728-2917` (`viewWhatIf`, `renderProjection`,
// `renderComparison`). The plan calls this the most useful screen in the app and
// `server/routes/whatif.py:8-18` agrees: every other view answers "where are we",
// this one answers "what should we do next", which is the question a funding
// conversation actually turns on.
//
// WHY THE TWO POSTS BELONG IN A READ-ONLY PHASE
// --------------------------------------------
// `/whatif/simulate` and `/whatif/simulate/compare` are POSTs that write nothing
// — `whatif.py:26-31` is explicit: no confirm gate, no audit row, no status
// change, deliberately runnable as often as you like during a workshop. They are
// POSTs only because the input is a list of asset ids too long for a query
// string. So they are reads, they carry no token flow, and this phase ports them
// whole.
//
// The console kept `whatifSelected` in a module-level `Set` that survived
// navigation and was mutated in place; here it is view state. A selection that
// outlives the screen it was made on is a trap — you come back, hit Project, and
// get a projection for sources you no longer remember choosing.
//
// Endpoints: GET /whatif/candidates?limit=40, POST /whatif/simulate,
// POST /whatif/simulate/compare.

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { GitBranch } from 'lucide-react'
import type { ReactNode } from 'react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import type { WhatIfComparison, WhatIfProjection, WhatIfUseCase } from '../types'

/** `$1.25M` from raw dollars. The candidate costs come off the API unscaled,
 *  while every *value* field is already in $M — mixing them up by one factor of
 *  a million is the one arithmetic error this screen cannot survive. */
function millions(dollars?: number | null): string {
  if (dollars == null) return '—'
  return `$${(dollars / 1e6).toFixed(2)}M`
}

function months(value?: number | null): string {
  return value != null ? `${value} mo` : '—'
}

export default function WhatIfView() {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // One slot for whichever of the two projections ran last, exactly as the
  // console's shared `#result` div behaved — running a comparison after a
  // projection replaces it rather than stacking two answers to two questions.
  const [result, setResult] = useState<
    { kind: 'projection'; data: WhatIfProjection } | { kind: 'comparison'; data: WhatIfComparison } | null
  >(null)
  const reportError = useApiErrorToast()

  const candidates = useQuery({
    queryKey: ['whatif-candidates'],
    queryFn: () => api.whatIfCandidates(40),
  })

  const simulate = useMutation({
    mutationFn: (ids: number[]) => api.whatIfSimulate(ids),
    onSuccess: (data) => setResult({ kind: 'projection', data }),
    onError: (error) => reportError(error, 'Could not run the projection.'),
  })

  // Each source on its own, plus the whole set: the question in the room is
  // almost always "which one first", not "all or nothing".
  const compare = useMutation({
    mutationFn: (ids: number[]) => api.whatIfCompare([...ids.map((id) => [id]), ids]),
    onSuccess: (data) => setResult({ kind: 'comparison', data }),
    onError: (error) => reportError(error, 'Could not compare those options.'),
  })

  const rows = candidates.data?.candidates ?? []
  const busy = simulate.isPending || compare.isPending

  const toggle = (id: number) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <GitBranch className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">What if we landed…</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          Pick one or more unlanded sources and see exactly which use cases become shovel-ready,
          what they are worth, and what it would cost. Nothing is written — this is a projection
          you can run as often as you like.
        </p>
      </div>

      <QueryState
        query={candidates}
        loading="Projecting every unlanded source…"
        empty={!rows.length}
        emptyMessage={
          candidates.data?.note ?? 'No unlanded source unblocks anything on its own.'
        }
      />

      {rows.length ? (
        <>
          <div className="card">
            <StatStrip
              stats={[
                { label: 'Unlanded sources', value: candidates.data?.evaluated ?? '—' },
                { label: 'With impact', value: candidates.data?.with_impact ?? '—' },
                { label: 'Best value / $M', value: rows[0].value_per_cost ?? '—' },
              ]}
            />
            {candidates.data?.note ? (
              <p className="text-xs text-navy-500 mt-3">{candidates.data.note}</p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn-primary text-sm"
              disabled={busy || selected.size === 0}
              onClick={() => simulate.mutate([...selected])}
            >
              {simulate.isPending ? 'Projecting…' : 'Project selected'}
            </button>
            <button
              className="btn-secondary text-sm"
              // Two is the floor the server enforces (`CompareIn.options`
              // min_length=2) and the floor the question needs — comparing one
              // option against nothing is just a projection.
              disabled={busy || selected.size < 2}
              title={
                selected.size < 2
                  ? 'Select at least two sources. Each is compared on its own, then all together.'
                  : undefined
              }
              onClick={() => compare.mutate([...selected])}
            >
              {compare.isPending ? 'Comparing…' : 'Compare selected'}
            </button>
            <button
              className="btn-secondary text-sm"
              disabled={busy || (selected.size === 0 && result == null)}
              onClick={() => {
                setSelected(new Set())
                setResult(null)
              }}
            >
              Clear
            </button>
            <span className="text-xs text-navy-500">
              {selected.size ? `${selected.size} selected` : 'Nothing selected'}
            </span>
          </div>

          <div className="card p-0 overflow-x-auto">
            <table className="w-full text-sm min-w-[860px]">
              <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                <tr>
                  <th className="w-9 px-3 py-2.5" />
                  <th className="text-left px-3 py-2.5 font-medium">Source</th>
                  <th className="text-left px-3 py-2.5 font-medium">Status</th>
                  <th className="text-right px-3 py-2.5 font-medium">Use cases</th>
                  <th className="text-right px-3 py-2.5 font-medium">Value / yr</th>
                  <th className="text-right px-3 py-2.5 font-medium">Cost</th>
                  <th className="text-right px-3 py-2.5 font-medium">Value per $M</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((candidate) => (
                  <tr
                    key={candidate.data_asset_id}
                    className="border-b border-navy-600 hover:bg-lava/5"
                  >
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        id={`whatif-${candidate.data_asset_id}`}
                        name={`whatif-${candidate.data_asset_id}`}
                        aria-label={`Include ${candidate.module} in the projection`}
                        checked={selected.has(candidate.data_asset_id)}
                        onChange={() => toggle(candidate.data_asset_id)}
                        className="cursor-pointer"
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="font-medium text-white">{candidate.module}</div>
                      <div className="text-xs text-navy-500">
                        {candidate.source}
                        {candidate.vendor ? ` · ${candidate.vendor}` : ''}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-navy-400">
                      {candidate.status ?? '—'}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {candidate.use_cases_unblocked}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                      {`$${candidate.value_unblocked_mm}M`}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs text-navy-500">
                      {`${(candidate.cost_low / 1e6).toFixed(2)}–${(
                        candidate.cost_high / 1e6
                      ).toFixed(2)}M`}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono font-semibold text-white">
                      {candidate.value_per_cost ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {result?.kind === 'projection' ? <Projection data={result.data} /> : null}
      {result?.kind === 'comparison' ? <Comparison data={result.data} /> : null}
    </div>
  )
}

function Projection({ data }: { data: WhatIfProjection }) {
  const cost = data.cost ?? {}
  const byLob = Object.entries(data.value_by_lob ?? {})

  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold text-white">
        {`If you landed ${data.sources.map((source) => source.module).join(' + ')}`}
      </h3>

      <div className="card">
        <StatStrip
          stats={[
            {
              label: 'Shovel-ready',
              value: `${data.baseline?.shovel_ready ?? '—'} → ${
                data.projected?.shovel_ready ?? '—'
              }`,
            },
            { label: 'Unlocked', value: data.unlocked_count },
            { label: 'Annual value', value: `$${data.annual_value_mm}M` },
            { label: 'Total cost', value: millions(cost.total_mid) },
            { label: 'Payback', value: months(data.payback_months) },
          ]}
        />
      </div>

      {data.unlocked.length ? (
        <Section title="Becomes shovel-ready">
          <UseCaseRows rows={data.unlocked} />
        </Section>
      ) : (
        <Banner kind="info">This combination unblocks nothing on its own.</Banner>
      )}

      {data.awaiting_count ? (
        <Section title="Data would be complete, but prerequisites remain">
          <p className="text-xs text-navy-500 px-4 pb-2">
            {`Worth $${data.awaiting_value_mm}M. The data gap closes; what is left is ` +
              'sequencing, not ingestion.'}
          </p>
          <UseCaseRows rows={data.data_complete_awaiting_prerequisites} showPending />
        </Section>
      ) : null}

      {byLob.length ? (
        <Section title="Value by line of business">
          <table className="w-full text-sm">
            <tbody>
              {byLob.map(([lob, value]) => (
                <tr key={lob} className="border-b border-navy-600">
                  <td className="px-4 py-2 text-navy-300">{lob}</td>
                  <td className="px-4 py-2 text-right font-mono text-navy-300">{`$${value}M`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}

      <p className="text-xs text-navy-500 max-w-[70ch]">
        {`Cost = sources $${cost.sources_low ?? 0}–$${cost.sources_high ?? 0} plus ` +
          `$${cost.delivery_mid ?? 0} to deliver what they unlock. Nobody lands data and ` +
          'stops, so the source price alone is not the investment.'}
      </p>
      <Banner kind="info">{data.note ?? 'Projection only — nothing was changed.'}</Banner>
    </div>
  )
}

function Comparison({ data }: { data: WhatIfComparison }) {
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-white">
        Options, best value per dollar first
      </h3>
      <div className="card p-0 overflow-x-auto">
        <table className="w-full text-sm min-w-[820px]">
          <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
            <tr>
              <th className="text-left px-3 py-2.5 font-medium">Land</th>
              <th className="text-right px-3 py-2.5 font-medium">Unlocks</th>
              <th className="text-right px-3 py-2.5 font-medium">Value / yr</th>
              <th className="text-right px-3 py-2.5 font-medium">Cost</th>
              <th className="text-right px-3 py-2.5 font-medium">Value per $M</th>
              <th className="text-right px-3 py-2.5 font-medium">Payback</th>
            </tr>
          </thead>
          <tbody>
            {(data.options ?? []).map((option, index) => (
              <tr
                key={option.option}
                className="border-b border-navy-600"
                // The server already sorted by value-per-dollar, so row 0 is the
                // recommendation. Tinting it is the whole answer at a glance.
                style={index === 0 ? { background: 'rgba(255,54,33,.07)' } : undefined}
              >
                <td className="px-3 py-2.5">
                  <div className="text-white">{option.sources.join(' + ')}</div>
                  {option.top_unlocked?.length ? (
                    <div className="text-xs text-navy-500">
                      {option.top_unlocked.slice(0, 3).join(' · ')}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                  {option.unlocked_count}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                  {`$${option.annual_value_mm}M`}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                  {millions(option.total_cost)}
                </td>
                <td className="px-3 py-2.5 text-right font-mono font-semibold text-white">
                  {option.value_per_cost ?? '—'}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs text-navy-400">
                  {months(option.payback_months)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.note ? <p className="text-xs text-navy-500 max-w-[70ch]">{data.note}</p> : null}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card p-0">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-navy-500 px-4 pt-4 pb-2">
        {title}
      </h4>
      {children}
    </div>
  )
}

/** The unlocked / awaiting lists. Same columns, so one component renders both —
 *  `showPending` swaps the effort column for what the use case still waits on. */
function UseCaseRows({
  rows,
  showPending,
}: {
  rows: WhatIfUseCase[]
  showPending?: boolean
}) {
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map((useCase) => (
          <tr key={useCase.id} className="border-b border-navy-600">
            <td className="px-4 py-2 text-white">{useCase.title}</td>
            {showPending ? null : (
              <td className="px-4 py-2 text-xs text-navy-500">{useCase.lob ?? ''}</td>
            )}
            <td className="px-4 py-2 text-xs text-navy-500">
              {showPending
                ? `still needs: ${useCase.still_pending?.join(', ') || '—'}`
                : `effort ${useCase.effort ?? 'M'}`}
            </td>
            <td className="px-4 py-2 text-right font-mono text-navy-300">
              {`$${useCase.value_mm}M`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
