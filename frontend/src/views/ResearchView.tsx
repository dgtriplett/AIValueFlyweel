// Company research, ported from the operator console (`console.js:1593-1809`).
//
// WHAT THIS SURFACE DOES
// ----------------------
// The value model ships as generic defaults describing a hypothetical 2M-customer
// utility, so every dollar figure is directionally meaningless until scaled to a
// real company. This screen names a utility, researches it, and calibrates the 34
// value assumptions that drive every number in the app — reviewing each proposed
// change WITH its provenance (confidence, what it was derived from, the reasoning)
// before applying the ones you trust.
//
// WHY IT SHARES THE INVALIDATION HOOK — the core of Phase 7
// ---------------------------------------------------------
// Applying a recalibration is confirm-gated and, once confirmed,
// `execute_apply_research` (server/routes/research.py) rewrites `value_assumptions`
// exactly as a manual PUT from `AssumptionsView` does. Both therefore MUST refresh
// the identical KPI queries, or a recalibration leaves the dashboard showing
// pre-calibration dollars while a manual edit refreshes them. `onApplied` calls
// `useAssumptionInvalidation()` — the ONE definition both paths consume.
//
// SAFETY PLUMBING (matches Phases 6)
// ----------------------------------
//  - Every call goes through the account-scoped `api.*` (axios `http`); no raw
//    fetch, no bare `<a href="/api/…">` — a missing account header does not raise
//    and would silently research/apply against the default account.
//  - The two token-spending calls (`runResearch`, `customerEnhancements`) spread
//    `NO_RETRY`: an automatic second POST after a 429 spends the budget the
//    `Retry-After` asked us to wait out, and research is minutes of model time.
//  - The apply flows through the shared `<ConfirmCard>`; this view owns the token
//    and invalidates in `onApplied`, per that component's contract.

import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { FlaskConical, Search, Sparkles } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { useApiErrorToast } from '../components/Toasts'
import { useAssumptionInvalidation } from '../hooks/useAssumptionInvalidation'
import { NO_RETRY } from '../lib/retry'
import type {
  AssumptionProposal,
  CustomerEnhancementResponse,
  ResearchApplyResponse,
} from '../types'

/** The console's `num()`: locale thousands separators, and a dash for nothing. */
function fmtNumber(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-US')
}

const CONFIDENCE_BADGE: Record<string, string> = {
  high: 'badge-success',
  medium: 'badge-warning',
  low: 'badge-danger',
}

function confidenceBadge(confidence?: string | null) {
  const level = confidence ?? 'low'
  return <span className={CONFIDENCE_BADGE[level] ?? 'badge-danger'}>{level}</span>
}

/** Percent-change cell: `null` means the current value was 0 (undefined change). */
function pctChange(value?: number | null) {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value}%`
}

export default function ResearchView() {
  const reportError = useApiErrorToast()
  // A recalibration re-quantifies the SAME KPIs a manual assumption edit does, so
  // it invalidates through the one shared hook. This is Phase 7's whole point.
  const invalidateKpis = useAssumptionInvalidation()

  const profileQuery = useQuery({
    queryKey: ['research-company'],
    queryFn: api.researchCompany,
  })
  const proposalsQuery = useQuery({
    queryKey: ['research-assumptions'],
    queryFn: api.researchAssumptions,
  })

  const [companyName, setCompanyName] = useState('')
  const [calibrateAssumptions, setCalibrateAssumptions] = useState(true)
  const [proposeLobs, setProposeLobs] = useState(true)
  const [enhancements, setEnhancements] = useState<CustomerEnhancementResponse | null>(null)
  const [applyCard, setApplyCard] = useState<ResearchApplyResponse | null>(null)

  const profile = profileQuery.data
  const proposals = proposalsQuery.data
  const rows: AssumptionProposal[] = proposals?.assumptions ?? []
  const pending = rows.filter((row) => !row.applied)
  const summary = proposals?.summary ?? {}

  // Seed the input from the stored profile once it lands, without clobbering what
  // the user is typing: only fill an untouched field.
  const storedName = profile?.company_name ?? ''
  const nameValue = companyName || storedName

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const toggle = (key: string) =>
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const research = useMutation({
    mutationFn: () =>
      api.runResearch({
        company_name: nameValue.trim(),
        calibrate_assumptions: calibrateAssumptions,
        propose_lobs: proposeLobs,
      }),
    ...NO_RETRY,
    onSuccess: () => {
      // The profile and the proposals were both rewritten server-side; re-read
      // them so the review table reflects the run just completed.
      void profileQuery.refetch()
      void proposalsQuery.refetch()
    },
    onError: (error) => reportError(error, 'Could not research this company.'),
  })

  const runAgent = useMutation({
    mutationFn: () => api.customerEnhancements(),
    ...NO_RETRY,
    onSuccess: setEnhancements,
    onError: (error) => reportError(error, 'The enhancement agent could not run.'),
  })

  // Stage a recalibration: POST /research/apply returns a confirm card; the shared
  // <ConfirmCard> consumes the token. Writes nothing until the user confirms.
  const prepareApply = useMutation({
    mutationFn: (keys: string[]) =>
      api.prepareResearchApply({ run_id: proposals?.run_id ?? null, keys }),
    ...NO_RETRY,
    onSuccess: setApplyCard,
    onError: (error) => reportError(error, 'Could not stage the recalibration.'),
  })

  const canResearch = nameValue.trim().length >= 2 && !research.isPending

  const trustedKeys = useMemo(
    () => pending.filter((row) => row.confidence !== 'low').map((row) => row.key),
    [pending],
  )

  const onRecalibrated = () => {
    invalidateKpis()
    // Applied rows drop out of "pending"; re-read so the table dims them and the
    // profile reflects the new calibrated count.
    void proposalsQuery.refetch()
    void profileQuery.refetch()
    setApplyCard(null)
    setSelected(new Set())
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Search className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Research</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[80ch]">
          Name a utility and the app researches it, then calibrates the 34 value
          assumptions that drive every dollar figure. Nothing is applied until you
          approve it.
        </p>
      </div>

      {/* Research form ----------------------------------------------------- */}
      <div className="card space-y-3">
        <h3 className="font-semibold text-white">
          {profile?.researched ? 'Re-run research' : 'Research a company'}
        </h3>
        <p className="text-sm text-navy-400 max-w-[80ch]">
          Shipped as generic defaults, the value model describes a hypothetical
          2-million-customer utility — so every number is directionally meaningless
          until it is scaled to a real company.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grow min-w-[240px]">
            <span className="text-xs text-navy-400 block mb-1">Company name</span>
            <input
              className="input-field w-full"
              placeholder="e.g. Eversource Energy"
              value={nameValue}
              onChange={(event) => setCompanyName(event.target.value)}
              aria-label="Company name"
            />
          </label>
          <button
            className="btn-primary text-sm"
            disabled={!canResearch}
            onClick={() => research.mutate()}
          >
            {research.isPending ? 'Researching…' : 'Research'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-navy-300">
            <input
              type="checkbox"
              checked={calibrateAssumptions}
              onChange={(event) => setCalibrateAssumptions(event.target.checked)}
            />
            Calibrate the value assumptions
          </label>
          <label className="flex items-center gap-2 text-sm text-navy-300">
            <input
              type="checkbox"
              checked={proposeLobs}
              onChange={(event) => setProposeLobs(event.target.checked)}
            />
            Propose lines of business
          </label>
        </div>
        <p className="text-xs text-navy-500">
          Use the full legal or operating name. Takes up to a minute — it is two
          model calls.
        </p>

        {research.data ? (
          <Banner kind="ok">
            Researched {research.data.company?.company_name ?? nameValue} —{' '}
            {fmtNumber(research.data.assumption_summary?.total)} assumption(s)
            calibrated by {research.data.model ?? 'heuristic'}. Nothing applied yet.
          </Banner>
        ) : null}

        {research.data?.warnings?.length ? (
          <div className="border-t border-navy-700 pt-2">
            <h4 className="text-sm font-semibold text-white mb-1">
              Notes from the research
            </h4>
            <ul className="list-disc ml-5 text-sm text-navy-400 space-y-1">
              {research.data.warnings.slice(0, 6).map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {/* Researched profile ------------------------------------------------ */}
      {profile?.researched ? (
        <div className="card space-y-2">
          <h3 className="font-semibold text-white">{profile.company_name}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
            <div>
              <div className="text-xs text-navy-500 uppercase tracking-wide">Type</div>
              <div className="text-navy-200">{profile.utility_type ?? '—'}</div>
            </div>
            <div>
              <div className="text-xs text-navy-500 uppercase tracking-wide">Segments</div>
              <div className="text-navy-200">
                {(profile.segments ?? []).join(', ') || '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-navy-500 uppercase tracking-wide">Market</div>
              <div className="text-navy-200">{profile.iso_rto ?? '—'}</div>
            </div>
          </div>
          {profile.description ? (
            <p className="text-sm text-navy-400">{profile.description}</p>
          ) : null}
          {profile.regulator ? (
            <p className="text-sm text-navy-400">
              <strong className="text-navy-300">Regulator:</strong> {profile.regulator}
            </p>
          ) : null}
          {profile.research_notes ? (
            <Banner kind="warn">Caveats from the research: {profile.research_notes}</Banner>
          ) : null}
        </div>
      ) : null}

      {/* Customer enhancement agent --------------------------------------- */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-lava" />
          <h3 className="font-semibold text-white">Customer enhancement agent</h3>
        </div>
        <p className="text-sm text-navy-400 max-w-[80ch]">
          Reviews the researched customer profile, current value assumptions, and
          portfolio to recommend assumption refinements and exactly 10 app
          enhancements. It does not apply anything.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            className="btn-secondary text-sm"
            disabled={runAgent.isPending}
            onClick={() => runAgent.mutate()}
          >
            {runAgent.isPending ? 'Researching…' : 'Run agent'}
          </button>
          <span className="text-xs text-navy-500">
            Use this before a sponsor readout or value-model review.
          </span>
        </div>
        {enhancements ? <EnhancementAgentResult result={enhancements} /> : null}
      </div>

      {/* Calibrated value assumptions review table ------------------------ */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="w-5 h-5 text-lava" />
          <h3 className="font-semibold text-white">Calibrated value assumptions</h3>
        </div>

        <QueryState
          query={proposalsQuery}
          loading="Loading the calibrated assumptions…"
          empty={!proposalsQuery.isLoading && rows.length === 0}
          emptyMessage={
            profile?.researched
              ? "No calibrated assumptions on record. Re-run research with 'Calibrate the value assumptions' checked."
              : 'Research a company above to calibrate its value assumptions.'
          }
        />

        {rows.length ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <Stat label="Calibrated" value={fmtNumber(summary.total)} />
              <Stat label="Changed" value={fmtNumber(summary.changed)} />
              <Stat label="High confidence" value={fmtNumber(summary.by_confidence?.high)} />
              <Stat label="Needs review" value={fmtNumber(summary.needs_review)} />
            </div>

            {summary.needs_review ? (
              <Banner kind="warn">
                {summary.needs_review} value(s) are industry-typical rather than
                company-specific. They are still scaled to the right size of utility,
                but check them before quoting a number that depends on one.
              </Banner>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <button
                className="btn-primary text-sm"
                disabled={!trustedKeys.length || prepareApply.isPending}
                onClick={() => prepareApply.mutate(trustedKeys)}
              >
                Apply high + medium confidence
              </button>
              <button
                className="btn-secondary text-sm"
                disabled={!pending.length || prepareApply.isPending}
                onClick={() => prepareApply.mutate(pending.map((row) => row.key))}
              >
                Apply all {fmtNumber(pending.length)}
              </button>
              <button
                className="btn-secondary text-sm"
                disabled={!selected.size || prepareApply.isPending}
                onClick={() => prepareApply.mutate(Array.from(selected))}
              >
                Apply selected
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-navy-500 uppercase tracking-wide border-b border-navy-700">
                    <th className="py-2 pr-2" />
                    <th className="py-2 pr-2">Assumption</th>
                    <th className="py-2 pr-2 text-right">Default</th>
                    <th className="py-2 pr-2 text-right">Calibrated</th>
                    <th className="py-2 pr-2 text-right">Change</th>
                    <th className="py-2 pr-2">Confidence</th>
                    <th className="py-2 pr-2">Basis &amp; reasoning</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.key}
                      className={`border-b border-navy-800 align-top ${
                        row.applied ? 'opacity-50' : ''
                      }`}
                    >
                      <td className="py-2 pr-2">
                        <input
                          type="checkbox"
                          aria-label={`Select ${row.label ?? row.key}`}
                          checked={selected.has(row.key)}
                          disabled={row.applied}
                          onChange={() => toggle(row.key)}
                        />
                      </td>
                      <td className="py-2 pr-2">
                        <div className="font-medium text-navy-100">
                          {row.label ?? row.key}
                        </div>
                        <div className="text-xs text-navy-500 font-mono">
                          {row.key}
                          {row.unit ? ` · ${row.unit}` : ''}
                        </div>
                      </td>
                      <td className="py-2 pr-2 text-right text-navy-300">
                        {fmtNumber(row.value_before)}
                      </td>
                      <td className="py-2 pr-2 text-right font-semibold text-white">
                        {fmtNumber(row.value_proposed)}
                      </td>
                      <td
                        className={`py-2 pr-2 text-right text-xs ${
                          (row.pct_change ?? 0) > 0 ? 'text-navy-200' : 'text-navy-500'
                        }`}
                      >
                        {pctChange(row.pct_change)}
                      </td>
                      <td className="py-2 pr-2">
                        {confidenceBadge(row.confidence)}
                        {row.applied ? (
                          <span className="badge-success ml-1">applied</span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-2 text-xs text-navy-400 max-w-[36ch]">
                        {row.basis ?? ''}
                        {row.rationale ? (
                          <div className="text-navy-500 mt-0.5">{row.rationale}</div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}

        {applyCard ? (
          <ConfirmCard
            token={applyCard.token}
            data={applyCard}
            summary={applyCard.summary}
            approveLabel="Confirm recalibration"
            onApplied={onRecalibrated}
            onCancel={() => setApplyCard(null)}
          />
        ) : null}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-navy-500 uppercase tracking-wide">{label}</div>
      <div className="text-lg font-bold text-white">{value}</div>
    </div>
  )
}

function EnhancementAgentResult({ result }: { result: CustomerEnhancementResponse }) {
  const refinements = result.assumption_refinements ?? []
  const enhancements = result.app_enhancements ?? []
  return (
    <div className="space-y-3 border-t border-navy-700 pt-3">
      {result.fallback_note ? <Banner kind="warn">{result.fallback_note}</Banner> : null}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Stat label="Model" value={result.model ?? 'heuristic'} />
        <Stat label="Uncalibrated" value={fmtNumber(result.generic_assumption_count)} />
        <Stat label="Refinements" value={fmtNumber(refinements.length)} />
        <Stat label="Enhancements" value={fmtNumber(enhancements.length)} />
      </div>

      {refinements.length ? (
        <div className="overflow-x-auto">
          <h4 className="text-sm font-semibold text-white mb-1">
            Assumption refinements to review
          </h4>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-navy-500 uppercase tracking-wide border-b border-navy-700">
                <th className="py-2 pr-2">Assumption</th>
                <th className="py-2 pr-2 text-right">Current</th>
                <th className="py-2 pr-2 text-right">Recommended</th>
                <th className="py-2 pr-2">Confidence</th>
                <th className="py-2 pr-2">Basis</th>
              </tr>
            </thead>
            <tbody>
              {refinements.map((refinement) => (
                <tr key={refinement.key} className="border-b border-navy-800 align-top">
                  <td className="py-2 pr-2">
                    <div className="font-medium text-navy-100">
                      {refinement.label ?? refinement.key}
                    </div>
                    <div className="text-xs text-navy-500 font-mono">{refinement.key}</div>
                  </td>
                  <td className="py-2 pr-2 text-right text-navy-300">
                    {fmtNumber(refinement.current_value)}
                  </td>
                  <td className="py-2 pr-2 text-right text-navy-200">
                    {refinement.recommended_value == null
                      ? 'review'
                      : fmtNumber(refinement.recommended_value)}
                  </td>
                  <td className="py-2 pr-2">{confidenceBadge(refinement.confidence)}</td>
                  <td className="py-2 pr-2 text-xs text-navy-400 max-w-[36ch]">
                    {refinement.basis ?? ''}
                    {refinement.rationale ? (
                      <div className="text-navy-500 mt-0.5">{refinement.rationale}</div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Banner kind="info">No assumption refinements returned.</Banner>
      )}

      {enhancements.length ? (
        <div className="overflow-x-auto">
          <h4 className="text-sm font-semibold text-white mb-1">10 app enhancements</h4>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-navy-500 uppercase tracking-wide border-b border-navy-700">
                <th className="py-2 pr-2">Enhancement</th>
                <th className="py-2 pr-2">Why it matters</th>
                <th className="py-2 pr-2">Implementation hint</th>
              </tr>
            </thead>
            <tbody>
              {enhancements.slice(0, 10).map((item, index) => (
                <tr key={item.title} className="border-b border-navy-800 align-top">
                  <td className="py-2 pr-2">
                    <div className="font-medium text-navy-100">
                      {index + 1}. {item.title}
                    </div>
                    {item.it_delivers ? (
                      <div className="text-xs text-navy-500">{item.it_delivers}</div>
                    ) : null}
                  </td>
                  <td className="py-2 pr-2 text-navy-300">{item.why ?? ''}</td>
                  <td className="py-2 pr-2 text-xs text-navy-400">
                    {item.implementation_hint ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {result.next ? <p className="text-xs text-navy-500">{result.next}</p> : null}
    </div>
  )
}
