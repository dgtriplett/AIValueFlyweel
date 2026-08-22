// The executive pack: a sponsor-ready read of the portfolio.
//
// Ported from `console.js:2922-3040` (`viewExecutive`, `exportUseCaseTable`).
//
// Read-only, including the download. `GET /exports/executive-pack` renders the
// pack; `GET /exports/executive-pack.md` renders the same thing as Markdown for a
// deck. Neither writes, so both belong in this phase — but the download goes
// through `api.executivePackMarkdown()` and `saveBlob`, NOT an `<a download>`
// href. An anchor bypasses axios and therefore bypasses the account interceptor,
// which is the failure §4.1 of TIER3_MIGRATION_PLAN.md is about: the server falls
// back to the default account when the header is missing, so a customer would
// download somebody else's numbers with no error anywhere.
//
// THE CALIBRATION CAVEAT IS NOT DECORATION
// ---------------------------------------
// The assumptions block exists so nobody quotes this pack externally on generic
// industry defaults. If any assumption is still generic the pack says so, in
// warning colours, next to the number it undermines. That is the most important
// sentence on the screen and it is why this is not just a table dump.
//
// Endpoints: GET /exports/executive-pack, GET /exports/executive-pack.md.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileText } from 'lucide-react'
import type { ReactNode } from 'react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast, useToast } from '../components/Toasts'
import { saveBlob } from '../lib/download'
import type { ExecutivePackUseCase } from '../types'

/** `2026-08-22 14:05:11` from the pack's ISO timestamp. Sliced, not reparsed:
 *  the server means UTC and says so in the caption. */
function generatedAt(iso?: string | null): string {
  return (iso ?? '').slice(0, 19).replace('T', ' ')
}

function confidenceClass(confidence?: string | null): string {
  if (confidence === 'high') return 'badge-low'
  if (confidence === 'medium') return 'badge-high'
  return 'badge-critical'
}

export default function ExecutiveView() {
  const queryClient = useQueryClient()
  const { show } = useToast()
  const reportError = useApiErrorToast()

  const pack = useQuery({ queryKey: ['executive-pack'], queryFn: api.executivePack })

  const download = useMutation({
    mutationFn: api.executivePackMarkdown,
    onSuccess: (blob) => {
      saveBlob(blob, 'grid-atlas-executive-pack.md')
      show({ kind: 'info', message: 'Executive pack downloaded.' })
    },
    onError: (error) => reportError(error, 'Could not build the pack.'),
  })

  const data = pack.data
  const metrics = data?.metrics ?? {}
  const assumptions = data?.assumptions ?? {}
  const whatifCandidates = data?.whatif?.candidates ?? []

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <FileText className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Executive brief</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          A sponsor-ready pack with the portfolio value, buildable use cases, blockers,
          assumption calibration status, and next actions.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-primary text-sm"
          disabled={download.isPending}
          onClick={() => download.mutate()}
        >
          <Download className="w-4 h-4" />
          {download.isPending ? 'Preparing…' : 'Download Markdown pack'}
        </button>
        <button
          className="btn-secondary text-sm"
          disabled={pack.isFetching}
          // Invalidation, not the console's `refresh: viewExecutive` re-render.
          // The pack is derived from the whole portfolio, so "refresh" means
          // "drop the cache entry", and react-query owns that.
          onClick={() => queryClient.invalidateQueries({ queryKey: ['executive-pack'] })}
        >
          {pack.isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <QueryState query={pack} loading="Building pack…" />

      {data ? (
        <>
          <div className="card">
            <h3 className="font-semibold text-white">
              {data.company?.company_name ?? 'Configured Utility'}
            </h3>
            <div className="mt-4">
              <StatStrip
                stats={[
                  { label: 'Total value', value: `$${metrics.total_value_mm ?? 0}M` },
                  {
                    label: 'Shovel-ready value',
                    value: `$${metrics.buildable_value_mm ?? 0}M`,
                  },
                  { label: 'Realized', value: `$${metrics.realized_value_mm ?? 0}M` },
                  { label: 'Use cases', value: metrics.use_cases_total ?? '—' },
                  { label: 'Blocked', value: metrics.blocked ?? '—' },
                ]}
              />
            </div>
            {data.generated_at ? (
              <p className="text-xs text-navy-500 mt-4">
                {`Generated ${generatedAt(data.generated_at)} UTC.`}
              </p>
            ) : null}
          </div>

          <PackSection title="Top buildable use cases">
            <UseCaseTable rows={data.top_buildable_use_cases ?? []} showConfidence />
          </PackSection>

          <PackSection title="Highest-value blockers">
            <UseCaseTable rows={data.top_blocked_or_awaiting_use_cases ?? []} />
          </PackSection>

          <div className="card space-y-4">
            <h3 className="font-semibold text-white">Value assumptions</h3>
            <StatStrip
              stats={[
                { label: 'Tracked', value: assumptions.total ?? '—' },
                { label: 'Customer-calibrated', value: assumptions.calibrated ?? '—' },
                { label: 'Still generic', value: assumptions.generic ?? '—' },
              ]}
            />
            {assumptions.generic ? (
              <Banner kind="warn">
                {`${assumptions.generic} assumption(s) still use generic industry defaults. ` +
                  'Run Company research before quoting the portfolio value externally.'}
              </Banner>
            ) : (
              <Banner kind="ok">Every value assumption is customer-calibrated.</Banner>
            )}
          </div>

          <PackSection title="What-if recommendations">
            {whatifCandidates.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[720px]">
                  <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                    <tr>
                      <th className="text-left px-3 py-2.5 font-medium">Source</th>
                      <th className="text-left px-3 py-2.5 font-medium">Module</th>
                      <th className="text-right px-3 py-2.5 font-medium">Use cases</th>
                      <th className="text-right px-3 py-2.5 font-medium">Value / yr</th>
                      <th className="text-right px-3 py-2.5 font-medium">Value per $M</th>
                    </tr>
                  </thead>
                  <tbody>
                    {whatifCandidates.slice(0, 8).map((candidate, index) => (
                      <tr
                        // The pack's candidates carry no id — it is a rendering,
                        // not a resource — so index is the only stable key.
                        key={`${candidate.module ?? ''}-${index}`}
                        className="border-b border-navy-600"
                      >
                        <td className="px-3 py-2.5 text-navy-300">{candidate.source ?? '—'}</td>
                        <td className="px-3 py-2.5 text-white">{candidate.module ?? '—'}</td>
                        <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                          {candidate.use_cases_unblocked ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                          {`$${candidate.value_unblocked_mm ?? 0}M`}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-white">
                          {candidate.value_per_cost ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="px-4 pb-4">
                <Banner kind="info">{data.whatif?.note ?? 'No recommendations.'}</Banner>
              </div>
            )}
          </PackSection>

          {(data.next_actions ?? []).length ? (
            <div className="card">
              <h3 className="text-sm font-semibold text-white mb-2">Recommended next actions</h3>
              <ul className="list-disc pl-5 space-y-1 text-sm text-navy-300">
                {(data.next_actions ?? []).map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function PackSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card p-0">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-navy-500 px-4 pt-4 pb-2">
        {title}
      </h3>
      {children}
    </div>
  )
}

/**
 * The buildable and blocked tables, from one component.
 *
 * The console had `exportUseCaseTable(rows, showConfidence)` for the same reason:
 * the two sections differ by one column. What each row must carry either way is
 * WHY it is where it is — the prerequisites it waits on and the data gaps behind
 * it — because a blocker list without the blockers is just a list of things that
 * are not happening.
 */
function UseCaseTable({
  rows,
  showConfidence,
}: {
  rows: ExecutivePackUseCase[]
  showConfidence?: boolean
}) {
  if (!rows.length) {
    return (
      <div className="px-4 pb-4">
        <Banner kind="info">None.</Banner>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm min-w-[760px]">
        <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
          <tr>
            <th className="text-left px-3 py-2.5 font-medium">Use case</th>
            <th className="text-left px-3 py-2.5 font-medium">LOB</th>
            <th className="text-left px-3 py-2.5 font-medium">State</th>
            <th className="text-right px-3 py-2.5 font-medium">Value / yr</th>
            {showConfidence ? (
              <th className="text-left px-3 py-2.5 font-medium">Confidence</th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((useCase) => (
            <tr key={useCase.id} className="border-b border-navy-600 hover:bg-lava/5">
              <td className="px-3 py-2.5">
                <div className="font-medium text-white">{useCase.title}</div>
                {useCase.pending_prereqs?.length ? (
                  <div className="text-xs text-navy-500">
                    {`Needs: ${useCase.pending_prereqs.map((prereq) => prereq.title).join(', ')}`}
                  </div>
                ) : null}
                {useCase.pending_domains?.length ? (
                  <div className="text-xs text-navy-500">
                    {`Data gaps: ${useCase.pending_domains
                      .map((domain) => domain.label ?? domain.name ?? '')
                      .join(', ')}`}
                  </div>
                ) : null}
              </td>
              <td className="px-3 py-2.5 text-xs text-navy-400">{useCase.lob ?? '—'}</td>
              <td className="px-3 py-2.5 text-xs text-navy-400">
                {(useCase.readiness ?? 'unknown').replace(/_/g, ' ')}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                {`$${useCase.value_mm ?? 0}M`}
              </td>
              {showConfidence ? (
                <td className="px-3 py-2.5">
                  <span className={confidenceClass(useCase.confidence)}>
                    {`${useCase.confidence ?? 'low'} · ${useCase.confidence_score ?? '—'}`}
                  </span>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
