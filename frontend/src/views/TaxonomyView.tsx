// Taxonomy: three dimensions that describe a source in ways nothing else does.
//
// Ported from `console.js:789-846` (`viewTaxonomy`); the catalog's sub-tab
// (`subTaxonomy`, `console.js:1399-1437`) is the same screen with the prose cut,
// and the standalone view is the one that explains itself, so this follows it.
//
// WHAT THE THREE DIMENSIONS BUY
// -----------------------------
// How the data arrives (`integration_pattern`), how operationally critical it is
// (`criticality`), and what kind of thing produces it (`vendor_type`).
// Classifications are effective-dated rather than updated in place
// (`server/routes/taxonomy.py:_supersede_and_insert`), so a reclassification keeps
// its history instead of quietly erasing what the estate used to look like.
//
// COVERAGE IS SHOWN BESIDE THE DISTRIBUTION ON PURPOSE
// ----------------------------------------------------
// `/taxonomy/coverage` exists because a distribution over 12 of 146 assets looks
// authoritative and means nothing — the route says so itself. So the per-dimension
// percentages sit above the value counts, and a distribution card is never shown
// without them.
//
// THE WRITE, AND WHAT IT COSTS
// ----------------------------
// `POST /taxonomy/classify` is `limiter("generate")` — burst 4, 12 per minute
// (`server/limits.py:136`) — because it calls the serving endpoint once per batch
// of 40 assets. So:
//
//   - `NO_RETRY`, spread at the mutation, so a 429 is reported rather than
//     compounded by three more identical POSTs;
//   - a 429 surfaces through `useApiErrorToast`, which carries the server's own
//     "wait 12s" out of `Retry-After` instead of flattening it to "unavailable";
//   - success invalidates `['taxonomy', 'taxonomy-coverage']` rather than the
//     console's `setTimeout(viewTaxonomy, 1200)`, which raced the write: 1.2s is a
//     guess, and a slow batch meant the refresh showed pre-write numbers.
//
// `warnings` is rendered, not swallowed. A run that wrote nothing because the
// endpoint was unreachable must not read as "everything was already classified" —
// the server de-duplicates the per-batch notes precisely so they can be shown.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles, Tags } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import { NO_RETRY } from '../lib/retry'
import type { ClassifyResponse } from '../types'

/** `integration_pattern` -> `integration pattern`. The server's keys are snake_case. */
export function dimensionLabel(dimension: string): string {
  return dimension.replace(/_/g, ' ')
}

/**
 * A dimension's values, highest count first, or `null` when it has none.
 *
 * `null` rather than an empty array because the distribution always carries all
 * three dimensions — `list_taxonomy` seeds `{d: {} for d in DIMENSIONS}` — so an
 * unclassified dimension arrives as an empty object and must render no card at
 * all. The console filtered on `Object.keys(v).length` for the same reason; doing
 * it here keeps the JSX from needing the test inline.
 */
export function rankedValues(
  values: Record<string, number> | undefined,
): [string, number][] | null {
  const entries = Object.entries(values ?? {})
  if (!entries.length) return null
  return entries.sort((a, b) => b[1] - a[1])
}

/** What a finished classify run wrote, in one sentence. */
export function classifySummary(result: ClassifyResponse): string {
  if (result.detail) return result.detail
  return (
    `${result.values_written ?? 0} classification(s) written across ` +
    `${result.assets_considered ?? 0} asset(s) in ${result.batches ?? 0} batch(es).`
  )
}

export default function TaxonomyView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [lastRun, setLastRun] = useState<ClassifyResponse | null>(null)

  const coverageQuery = useQuery({
    queryKey: ['taxonomy-coverage'],
    queryFn: api.taxonomyCoverage,
  })
  const taxonomyQuery = useQuery({ queryKey: ['taxonomy'], queryFn: api.taxonomy })

  const classify = useMutation({
    mutationFn: () => api.classifyTaxonomy(200),
    // `generate`-limited: never auto-retry. See the header note.
    ...NO_RETRY,
    onSuccess: (result) => {
      setLastRun(result)
      queryClient.invalidateQueries({ queryKey: ['taxonomy'] })
      queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage'] })
      // Ingest-effort hints on an asset derive from `integration_pattern`, so the
      // catalog's numbers move with a classify run too.
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
    },
    onError: (error) => {
      setLastRun(null)
      reportError(error, 'Could not classify the catalog.')
    },
  })

  const coverage = coverageQuery.data
  const distribution = taxonomyQuery.data?.distribution ?? {}
  // Keyed off the response rather than a hardcoded triple: a fourth dimension
  // added server-side should appear here without a frontend change.
  const dimensions = Object.keys(coverage?.by_dimension ?? distribution)
  const warnings = lastRun?.warnings ?? []

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Tags className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Taxonomy</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          Three dimensions that describe a source in ways nothing else here does: how the data
          arrives, how operationally critical it is, and what kind of thing produces it.
          Classifications are effective-dated, so changing one keeps its history.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Assets', value: coverage?.total_assets ?? '—' },
            { label: 'Fully classified', value: coverage?.fully_classified ?? '—' },
            ...dimensions.map((dimension) => ({
              label: dimensionLabel(dimension),
              value: `${coverage?.by_dimension?.[dimension]?.pct ?? 0}%`,
            })),
          ]}
        />
      </div>

      <div className="card flex flex-wrap items-center gap-3">
        <button
          className="btn-primary text-sm flex items-center gap-1.5"
          disabled={classify.isPending}
          onClick={() => classify.mutate()}
        >
          <Sparkles className="w-4 h-4" />
          {classify.isPending ? 'Classifying…' : 'Classify unlabelled assets'}
        </button>
        <span className="text-xs text-navy-500 max-w-[56ch]">
          Manual classifications are never overwritten — a human decision outranks the model's.
          Runs up to 200 assets, 40 per batch.
        </span>
      </div>

      {lastRun ? (
        <Banner kind={lastRun.values_written ? 'ok' : 'info'}>{classifySummary(lastRun)}</Banner>
      ) : null}

      {warnings.length ? (
        <Banner kind="warn">
          <div className="font-medium">The run reported problems:</div>
          <ul className="list-disc pl-4 mt-1 space-y-0.5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <QueryState
        query={taxonomyQuery}
        loading="Loading taxonomy…"
        empty={!dimensions.some((dimension) => rankedValues(distribution[dimension]))}
        emptyMessage="Nothing classified yet. Classify unlabelled assets to populate this."
      />

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {dimensions.map((dimension) => {
          const ranked = rankedValues(distribution[dimension])
          if (!ranked) return null
          const stats = coverage?.by_dimension?.[dimension]
          return (
            <div key={dimension} className="card p-0">
              <div className="px-4 pt-4 pb-2">
                <h3 className="text-sm font-semibold text-white capitalize">
                  {dimensionLabel(dimension)}
                </h3>
                {stats ? (
                  <div className="text-xs text-navy-500">
                    {`${stats.classified} of ${coverage?.total_assets ?? 0} assets · ${stats.pct}%`}
                  </div>
                ) : null}
              </div>
              <table className="w-full text-sm">
                <tbody>
                  {ranked.map(([value, count]) => (
                    <tr key={value} className="border-t border-navy-600">
                      <td className="px-4 py-1.5 text-navy-300">{value}</td>
                      <td className="px-4 py-1.5 text-right font-mono text-navy-300">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        })}
      </div>
    </div>
  )
}
