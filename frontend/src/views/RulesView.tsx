// Naming rules: teach the app a utility's catalog conventions once.
//
// Ported from `console.js:1504-1590` (`subRules`, the catalog's "Naming rules"
// sub-tab). Endpoints are at `/api/rules`, NOT `/api/inventory/rules`:
// `server/routes/inventory.py:33` declares `APIRouter(tags=[...])` with no prefix,
// the same trap the artifacts endpoints document in `api.ts`.
//
// WHY THIS SCREEN EXISTS
// ---------------------
// Every utility names its catalogs by a convention nobody wrote down — `prod_`, a
// region code in position three, an operating-company prefix. After a sweep those
// conventions are the difference between 40,000 anonymous tables and 40,000 you can
// filter. The app cannot guess them and hand-correcting thousands of rows is not a
// real option, so a user states the rule once and it applies to everything
// discovered, including future sweeps (`server/rules.py`).
//
// FIRST MATCH WINS PER DIMENSION, which is why priority is editable rather than
// implicit: real conventions have exceptions ("everything starting `dev_` is dev,
// EXCEPT `dev_shared` which is prod"), and expressing one means ordering the
// specific rule ahead of the general one. The console's add form omitted priority
// entirely and every rule landed on the server default of 100 — a tie the ordering
// then broke by `id`, i.e. by whichever was typed first. So priority is on the form.
//
// THE TWO RATE-LIMIT CLASSES ON THIS SCREEN
// -----------------------------------------
// `POST /rules/test` is `limiter("generate")` — burst 4, 12/min
// (`server/limits.py:136`) — because it runs a warehouse query against
// `discovered_tables`. The CRUD writes are unlimited. Both mutations that can hit a
// limit spread `NO_RETRY` and report through `useApiErrorToast`, so a 429 arrives
// as the server's own "wait 12s" rather than as a generic failure, and is never
// compounded by an automatic second attempt.
//
// Deletes and adds invalidate `['rules']` instead of the console's
// `setTimeout(viewCatalog, 700..900)`. The console's delay was a guess that raced
// the write, and its delete didn't refetch at all — it faded the row to
// `opacity: 0.4` and left a deleted rule on screen until a manual reload.

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Plus, Shield, Sprout, Trash2 } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import { OPTION_STYLE, SELECT_CLASS } from '../constants'
import { NO_RETRY } from '../lib/retry'
import type { RuleInput, RuleTestResponse } from '../types'

/** The server's default when a rule omits one (`inventory.py`'s `RuleIn`). */
const DEFAULT_PRIORITY = 100

interface Draft {
  dimension: string
  field: string
  match_type: string
  pattern: string
  value: string
  priority: string
  notes: string
}

const EMPTY_DRAFT: Draft = {
  dimension: '',
  field: '',
  match_type: '',
  pattern: '',
  value: '',
  priority: String(DEFAULT_PRIORITY),
  notes: '',
}

/**
 * Turn the form into a `RuleIn` body, or say what is missing.
 *
 * Client-side validation duplicates `server/rules.py:validate_rule` deliberately
 * narrowly: only the checks that save a round trip and a 422 on an obvious mistake
 * (an empty pattern, a priority that is not a number). The server remains the
 * authority — its `RuleError` messages are shown verbatim when they arrive, because
 * a bad regex is only detectable where it is compiled.
 *
 * The `ignore` dimension is the one case where a blank `value` is correct rather
 * than incomplete: an ignore rule's effect IS exclusion, so it assigns nothing.
 * The console said this in a footnote under the form and then let you submit a
 * blank `value` on any dimension, which 422s for every other one.
 */
export function draftToRule(draft: Draft): { rule: RuleInput } | { error: string } {
  if (!draft.dimension) return { error: 'Choose what the rule decides.' }
  if (!draft.field) return { error: 'Choose which field the rule looks at.' }
  if (!draft.match_type) return { error: 'Choose how the pattern should match.' }
  if (!draft.pattern.trim()) return { error: 'A rule needs a pattern to match on.' }

  const value = draft.value.trim()
  if (!value && draft.dimension !== 'ignore') {
    return { error: `An ${draft.dimension} rule has to assign a value.` }
  }

  // Trimmed and tested for emptiness FIRST, because `Number('')` is 0 — a blank
  // box would otherwise validate as priority 0 and make the rule the
  // highest-priority one in the whole set, which is the opposite of "unspecified".
  const priority = Number(draft.priority.trim())
  if (!draft.priority.trim() || !Number.isInteger(priority) || priority < 0) {
    return { error: 'Priority has to be a whole number — lower runs first.' }
  }

  return {
    rule: {
      dimension: draft.dimension,
      field: draft.field,
      match_type: draft.match_type,
      pattern: draft.pattern.trim(),
      // `null`, not `''`: an ignore rule assigns nothing, and an empty string is a
      // value the server would store and then try to classify rows as.
      value: value || null,
      priority,
      notes: draft.notes.trim() || null,
    },
  }
}

/**
 * The one sentence a dry run is worth reading for.
 *
 * `unmatched` is the number that says whether the rules actually work, and
 * `sample_source` is what makes the rest interpretable: `discovered_tables` means
 * they were tried against the real estate, `supplied` means against rows somebody
 * passed in. A summary without that distinction is not evidence of anything.
 */
export function testSummary(result: RuleTestResponse): string {
  const summary = result.summary ?? { total: 0, ignored: 0, unmatched: 0, by_dimension: {} }
  return (
    `${result.rules_applied} rule(s) over ${summary.total} row(s) from ` +
    `${result.sample_source}: ${summary.ignored} ignored, ${summary.unmatched} matched nothing.`
  )
}

export default function RulesView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  const rulesQuery = useQuery({ queryKey: ['rules'], queryFn: api.rules })
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [formError, setFormError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<RuleTestResponse | null>(null)
  const [seeded, setSeeded] = useState<string | null>(null)

  const vocabulary = rulesQuery.data?.vocabulary
  const rules = rulesQuery.data?.rules ?? []

  // Applying a rule set is what the dry run is a preview of, so both counts are
  // worth having above the table.
  const active = rules.filter((rule) => rule.is_active !== false).length
  const ignoreRules = rules.filter((rule) => rule.dimension === 'ignore').length

  const invalidateRules = () => {
    queryClient.invalidateQueries({ queryKey: ['rules'] })
  }

  const add = useMutation({
    mutationFn: (rule: RuleInput) => api.createRule(rule),
    onSuccess: () => {
      invalidateRules()
      // Keep the dimension/field/match selections: rules arrive in families
      // ("everything in this catalog naming scheme"), and re-picking three selects
      // per rule is the friction that makes people stop after one.
      setDraft((previous) => ({ ...previous, pattern: '', value: '', notes: '' }))
      setFormError(null)
    },
    onError: (error) => reportError(error, 'Could not add that rule.'),
  })

  const remove = useMutation({
    mutationFn: (ruleId: number) => api.deleteRule(ruleId),
    onSuccess: invalidateRules,
    onError: (error) => reportError(error, 'Could not remove that rule.'),
  })

  const seed = useMutation({
    mutationFn: api.seedRules,
    onSuccess: (result) => {
      invalidateRules()
      // Idempotent, so 0 created is a real and useful answer — "you already have
      // these" — not a failure. The console reported it as "Added 0 rule(s)."
      setSeeded(
        result.created
          ? `Added ${result.created} of ${result.total_seeds} common convention(s).`
          : `You already have all ${result.total_seeds} common conventions.`,
      )
    },
    onError: (error) => reportError(error, 'Could not load the common conventions.'),
  })

  const test = useMutation({
    mutationFn: () => api.testRules(100),
    // `generate`-limited, and it queries the warehouse. See the header note.
    ...NO_RETRY,
    onSuccess: setTestResult,
    onError: (error) => {
      // Cleared, so a stale summary from an earlier run cannot be mistaken for the
      // result of the run that just failed.
      setTestResult(null)
      reportError(error, 'Could not dry-run the rules.')
    },
  })

  const submit = () => {
    const parsed = draftToRule(draft)
    if ('error' in parsed) {
      setFormError(parsed.error)
      return
    }
    setFormError(null)
    add.mutate(parsed.rule)
  }

  const byDimension = useMemo(
    () => Object.entries(testResult?.summary?.by_dimension ?? {}),
    [testResult],
  )

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Naming rules</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          Teach the app your catalog naming conventions once, instead of hand-correcting thousands
          of discovered rows. First match wins per dimension, so a specific rule can be ordered
          ahead of a general one — which is how real conventions work.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Rules', value: rulesQuery.isLoading ? '—' : rules.length },
            { label: 'Active', value: rulesQuery.isLoading ? '—' : active },
            { label: 'Exclusions', value: rulesQuery.isLoading ? '—' : ignoreRules },
          ]}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-secondary text-sm flex items-center gap-1.5"
          disabled={seed.isPending}
          onClick={() => seed.mutate()}
        >
          <Sprout className="w-4 h-4" />
          {seed.isPending ? 'Loading…' : 'Load common conventions'}
        </button>
        <button
          className="btn-secondary text-sm flex items-center gap-1.5"
          disabled={test.isPending || !active}
          onClick={() => test.mutate()}
          title={active ? undefined : 'Add or seed a rule first — there is nothing to test.'}
        >
          <FlaskConical className="w-4 h-4" />
          {test.isPending ? 'Testing…' : 'Test against real tables'}
        </button>
      </div>

      {seeded ? <Banner kind="ok">{seeded}</Banner> : null}

      <div className="card">
        <h3 className="text-sm font-semibold text-white mb-3">Add a rule</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Decides</span>
            <select
              id="rule-dimension"
              name="rule-dimension"
              className={`${SELECT_CLASS} w-full`}
              value={draft.dimension}
              onChange={(event) => setDraft({ ...draft, dimension: event.target.value })}
            >
              <option value="" style={OPTION_STYLE}>
                Choose…
              </option>
              {(vocabulary?.dimensions ?? []).map((dimension) => (
                <option key={dimension} value={dimension} style={OPTION_STYLE}>
                  {dimension}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Looks at</span>
            <select
              id="rule-field"
              name="rule-field"
              className={`${SELECT_CLASS} w-full`}
              value={draft.field}
              onChange={(event) => setDraft({ ...draft, field: event.target.value })}
            >
              <option value="" style={OPTION_STYLE}>
                Choose…
              </option>
              {(vocabulary?.fields ?? []).map((field) => (
                <option key={field} value={field} style={OPTION_STYLE}>
                  {field}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Match</span>
            <select
              id="rule-match-type"
              name="rule-match-type"
              className={`${SELECT_CLASS} w-full`}
              value={draft.match_type}
              onChange={(event) => setDraft({ ...draft, match_type: event.target.value })}
            >
              <option value="" style={OPTION_STYLE}>
                Choose…
              </option>
              {(vocabulary?.match_types ?? []).map((matchType) => (
                <option key={matchType} value={matchType} style={OPTION_STYLE}>
                  {matchType}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Pattern</span>
            <input
              id="rule-pattern"
              name="rule-pattern"
              className="input-field w-full font-mono"
              placeholder="prod_"
              value={draft.pattern}
              onChange={(event) => setDraft({ ...draft, pattern: event.target.value })}
            />
          </label>

          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Assign</span>
            <input
              id="rule-value"
              name="rule-value"
              className="input-field w-full"
              placeholder={draft.dimension === 'ignore' ? 'nothing — this excludes' : 'production'}
              disabled={draft.dimension === 'ignore'}
              value={draft.dimension === 'ignore' ? '' : draft.value}
              onChange={(event) => setDraft({ ...draft, value: event.target.value })}
            />
          </label>

          <label className="block">
            <span className="text-xs text-navy-400 block mb-1">Priority (lower runs first)</span>
            <input
              id="rule-priority"
              name="rule-priority"
              type="number"
              min={0}
              className="input-field w-full"
              value={draft.priority}
              onChange={(event) => setDraft({ ...draft, priority: event.target.value })}
            />
          </label>

          <label className="block sm:col-span-2">
            <span className="text-xs text-navy-400 block mb-1">Note (optional)</span>
            <input
              id="rule-notes"
              name="rule-notes"
              className="input-field w-full"
              placeholder="Why this convention exists"
              value={draft.notes}
              onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
            />
          </label>

          <div className="flex items-end">
            <button
              className="btn-primary text-sm flex items-center gap-1.5"
              disabled={add.isPending}
              onClick={submit}
            >
              <Plus className="w-4 h-4" />
              {add.isPending ? 'Adding…' : 'Add rule'}
            </button>
          </div>
        </div>

        <p className="text-xs text-navy-500 mt-3">
          Choose the <code>ignore</code> dimension to exclude matching rows entirely — an ignore
          rule assigns nothing, so <em>Assign</em> is not used.
        </p>

        {formError ? (
          <div className="mt-3">
            <Banner kind="err">{formError}</Banner>
          </div>
        ) : null}
      </div>

      {testResult ? (
        <div className="space-y-3">
          <Banner kind={testResult.summary?.unmatched ? 'warn' : 'ok'}>
            {testSummary(testResult)}
          </Banner>
          {byDimension.length ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {byDimension.map(([dimension, values]) => (
                <div key={dimension} className="card p-0">
                  <h3 className="text-sm font-semibold text-white px-4 pt-4 pb-2 capitalize">
                    {dimension.replace(/_/g, ' ')}
                  </h3>
                  <table className="w-full text-sm">
                    <tbody>
                      {Object.entries(values).map(([value, count]) => (
                        <tr key={value} className="border-t border-navy-600">
                          <td className="px-4 py-1.5 text-navy-300">{value}</td>
                          <td className="px-4 py-1.5 text-right font-mono text-navy-300">
                            {count}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <QueryState
        query={rulesQuery}
        loading="Loading rules…"
        empty={!rules.length}
        emptyMessage="No rules yet. Load the common conventions to start, then add your own."
      />

      {rules.length ? (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm min-w-[820px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Decides</th>
                <th className="text-left px-3 py-2.5 font-medium">Field</th>
                <th className="text-left px-3 py-2.5 font-medium">Match</th>
                <th className="text-left px-3 py-2.5 font-medium">Pattern</th>
                <th className="text-left px-3 py-2.5 font-medium">Assigns</th>
                <th className="text-right px-3 py-2.5 font-medium">Priority</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => {
                // Per-row, so deleting one rule does not disable every other
                // Remove button while it is in flight.
                const deleting = remove.isPending && remove.variables === rule.id
                return (
                  <tr key={rule.id} className="border-b border-navy-600 hover:bg-lava/5">
                    <td className="px-3 py-2.5 text-navy-300">
                      {rule.dimension}
                      {rule.is_active === false ? (
                        <span className="badge-muted ml-2">inactive</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-navy-400">{rule.field}</td>
                    <td className="px-3 py-2.5 text-xs text-navy-500">{rule.match_type}</td>
                    <td className="px-3 py-2.5 font-mono text-xs text-white">{rule.pattern}</td>
                    <td className="px-3 py-2.5 text-navy-300">{rule.value ?? '—'}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-navy-400">
                      {rule.priority}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <button
                        className="btn-secondary text-xs"
                        aria-label={`Remove the ${rule.dimension} rule matching ${rule.pattern}`}
                        disabled={deleting}
                        onClick={() => remove.mutate(rule.id)}
                      >
                        {deleting ? (
                          'Removing…'
                        ) : (
                          <span className="flex items-center gap-1">
                            <Trash2 className="w-3 h-3" /> Remove
                          </span>
                        )}
                      </button>
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
