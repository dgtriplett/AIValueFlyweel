// Source mapping: the review queue for labels the normalizer could not resolve.
//
// Ported from `console.js:669-720` (`viewAliases`), which the catalog's "Source
// mapping" sub-tab (`subMapping`, `console.js:1362-1397`) duplicated almost
// verbatim. The standalone view is the fuller of the two — it says why a
// correction is permanent — so that is what this follows.
//
// WHY THIS SCREEN EXISTS
// ---------------------
// A discovery sweep produces raw source-system labels ("SAP-PM", "sappm_prod",
// "Maximo v7") and the normalizer resolves each to a canonical category. It is
// confident most of the time. `?needs_review=true` returns exactly the residue:
// unresolved labels, low-confidence guesses, and anything that fell through to
// `Other` (`server/routes/ingestion.py:898-912`). Correcting one here sets
// `is_user_edited`, which is what makes a later sweep skip the row rather than
// overwrite the human decision — so the queue drains permanently instead of
// refilling every time someone re-runs discovery.
//
// WHAT CHANGED FROM THE CONSOLE, AND WHY
// --------------------------------------
// The console faded the corrected row to `opacity: 0.45` and left it in the DOM,
// because it had no way to refetch one row without rebuilding the whole view. That
// left a saved row sitting in a queue it no longer belongs to, greyed out, until a
// manual reload. Here the mutation invalidates `['source-aliases']` and the row
// leaves the queue on its own — which is both the correct state and the feedback.
//
// The per-row draft is deliberately NOT initialized to `alias.canonical`. The
// console's select defaulted to its first option ("Other") regardless of what the
// row was already mapped to, so tabbing past a row and clicking Save silently
// re-mapped it to `Other`. A row with no explicit choice has no Save button here.

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Network } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import { OPTION_STYLE, SELECT_CLASS } from '../constants'
import type { SourceAlias } from '../types'

/**
 * The canonical vocabulary the server will accept for a correction.
 *
 * Derived from `/data-assets` rather than hardcoded, exactly as the console did
 * (`console.js:672-674`), because `patch_alias` validates against the live
 * `_canonical_vocabulary()` and 422s anything else — a stale hardcoded list would
 * offer a category the server rejects. `Other` is prepended because
 * `server/normalize.py`'s sentinel is always valid even when no asset carries it.
 *
 * Exported for the unit tests: this is the one piece of logic on this screen that
 * can be wrong in a way nothing on the page would show.
 */
export function canonicalOptions(categories: (string | null | undefined)[]): string[] {
  const known = [...new Set(categories.filter((c): c is string => Boolean(c)))].sort()
  return ['Other', ...known.filter((c) => c !== 'Other')]
}

/** The confidence pill's tone. `low` and absent are both bad news, differently. */
function confidenceBadge(confidence?: string | null): string {
  if (confidence === 'high') return 'badge-low'
  if (confidence === 'low') return 'badge-critical'
  return 'badge-medium'
}

export default function SourceMappingView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  const aliasesQuery = useQuery({
    queryKey: ['source-aliases'],
    queryFn: () => api.sourceAliases(true, 200),
  })
  // The vocabulary. A failure here must not blank the queue — `Other` alone is
  // still a usable correction — so the select falls back to the sentinel rather
  // than the whole view failing on a secondary request.
  const assetsQuery = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })

  const [drafts, setDrafts] = useState<Record<number, string>>({})

  const options = useMemo(
    () => canonicalOptions((assetsQuery.data ?? []).map((asset) => asset.source_category)),
    [assetsQuery.data],
  )

  const fix = useMutation({
    mutationFn: ({ id, canonical }: { id: number; canonical: string }) =>
      api.patchSourceAlias(id, canonical),
    onSuccess: (_row, variables) => {
      // The corrected row drops out of the review queue server-side, and the
      // normalizer's own counts move with it, so both are refetched. The console
      // faded the row in place instead and left the queue wrong until a reload.
      queryClient.invalidateQueries({ queryKey: ['source-aliases'] })
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      setDrafts((previous) => {
        const next = { ...previous }
        delete next[variables.id]
        return next
      })
    },
    onError: (error) => reportError(error, 'Could not pin that mapping.'),
  })

  const aliases = aliasesQuery.data ?? []
  const unresolved = aliases.filter((alias) => !alias.canonical).length
  const lowConfidence = aliases.filter((alias) => alias.confidence === 'low').length

  const save = (alias: SourceAlias) => {
    const canonical = drafts[alias.id]
    // No draft means the user has not chosen: saving here would write whatever the
    // select happened to show first, which is the console's silent-remap bug.
    if (!canonical || canonical === alias.canonical) return
    fix.mutate({ id: alias.id, canonical })
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Network className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Source mapping</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[75ch]">
          Raw source-system labels the normalizer could not confidently resolve. Correcting one
          here pins it permanently — later discovery runs will never overwrite a human decision,
          so this queue drains instead of refilling.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Needs review', value: aliasesQuery.isLoading ? '—' : aliases.length },
            { label: 'Unresolved', value: aliasesQuery.isLoading ? '—' : unresolved },
            { label: 'Low confidence', value: aliasesQuery.isLoading ? '—' : lowConfidence },
          ]}
        />
      </div>

      {assetsQuery.isError ? (
        <Banner kind="warn">
          Couldn't load the source categories, so only <code>Other</code> can be assigned right
          now. Reload to get the full list back.
        </Banner>
      ) : null}

      <QueryState
        query={aliasesQuery}
        loading="Loading mappings…"
        empty={!aliases.length}
        emptyMessage="Nothing needs review — every source label is confidently mapped."
      />

      {aliases.length ? (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm min-w-[760px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Raw label</th>
                <th className="text-left px-3 py-2.5 font-medium">Mapped to</th>
                <th className="text-left px-3 py-2.5 font-medium">How</th>
                <th className="text-left px-3 py-2.5 font-medium">Confidence</th>
                <th className="text-left px-3 py-2.5 font-medium">Correct it</th>
              </tr>
            </thead>
            <tbody>
              {aliases.map((alias) => {
                const draft = drafts[alias.id] ?? ''
                // Only this row's pending write disables this row's button. A
                // single `fix.isPending` would grey out every button in the table
                // while one saved, which reads as the whole screen locking up.
                const saving =
                  fix.isPending && fix.variables?.id === alias.id
                return (
                  <tr key={alias.id} className="border-b border-navy-600 hover:bg-lava/5">
                    <td className="px-3 py-2.5 font-mono text-white">{alias.raw}</td>
                    <td className="px-3 py-2.5 text-navy-300">{alias.canonical ?? '—'}</td>
                    <td className="px-3 py-2.5 text-xs text-navy-500">{alias.mapped_by ?? '—'}</td>
                    <td className="px-3 py-2.5">
                      <span className={confidenceBadge(alias.confidence)}>
                        {alias.confidence ?? 'none'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <select
                          id={`alias-canonical-${alias.id}`}
                          name={`alias-canonical-${alias.id}`}
                          aria-label={`Map ${alias.raw} to a source category`}
                          className={SELECT_CLASS}
                          value={draft}
                          onChange={(event) =>
                            setDrafts((previous) => ({
                              ...previous,
                              [alias.id]: event.target.value,
                            }))
                          }
                        >
                          <option value="" style={OPTION_STYLE}>
                            Choose a category…
                          </option>
                          {options.map((category) => (
                            <option key={category} value={category} style={OPTION_STYLE}>
                              {category}
                            </option>
                          ))}
                        </select>
                        <button
                          className="btn-secondary text-xs"
                          disabled={!draft || draft === alias.canonical || saving}
                          onClick={() => save(alias)}
                        >
                          {saving ? (
                            'Saving…'
                          ) : (
                            <span className="flex items-center gap-1">
                              <Check className="w-3 h-3" /> Pin
                            </span>
                          )}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
