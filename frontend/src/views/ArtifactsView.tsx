// Artifacts: what has already been built on the platform.
//
// Ported from `console.js:1461-1502` (`subArtifacts`, the catalog's "What's built"
// sub-tab).
//
// THE UNATTRIBUTED SET IS THE POINT
// ---------------------------------
// A list of jobs and dashboards is inventory. The interesting slice is the one no
// use case claims, because it is one of two things and both matter: shadow work
// the portfolio does not know about (worth adding, and it proves demand), or
// something abandoned that still costs money to run. The console said this and it
// is the reason the view exists, so the interpretation is kept as prose rather
// than left for the reader to infer from a count.
//
// Split further by whether it ran in the last 30 days: `active_unclaimed` is the
// shadow-work case, and it is the one that reads as a warning.
//
// WHAT THIS PORT LEAVES OUT, AND WHY
// ----------------------------------
// The console's "Scan the workspace" button (`console.js:1493-1500`) is a write:
// POST /artifacts/sync, `limiter("sweep")` — the tightest limit class in
// `server/limits.py` at 2 per window, because it walks system tables. This phase
// is read-only by contract so it is not ported.
//
// TODO(Tier 3 Phase 5 — curation writes): port POST /artifacts/sync here. It fits
// that phase's shape (a write, a rate limit, no confirm token) and needs its
// disciplines: `NO_RETRY` from `lib/retry.ts` so a `sweep` refusal is not
// compounded, react-query invalidation of `['artifacts', 'artifacts-unattributed']`
// instead of the console's `setTimeout(viewCatalog, 1200)`, and the `notes` array
// surfaced — an empty scan with unreadable system tables must not read as "nothing
// is built here".
//
// Endpoints: GET /artifacts?limit=200, GET /artifacts/unattributed?limit=30.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Archive } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import type { Artifact } from '../types'

type Scope = 'all' | 'unclaimed'

function ranLabel(artifact: Artifact): string {
  const runs = artifact.run_count_30d
  if (runs == null) return '—'
  return runs > 0 ? `${runs} run(s) / 30d` : 'not run in 30d'
}

export default function ArtifactsView() {
  const [scope, setScope] = useState<Scope>('all')

  const all = useQuery({ queryKey: ['artifacts'], queryFn: () => api.artifacts(200) })
  const unattributed = useQuery({
    queryKey: ['artifacts-unattributed'],
    queryFn: () => api.unattributedArtifacts(30),
  })

  const summary = all.data?.summary ?? {}
  const unclaimedSummary = unattributed.data?.summary ?? {}
  const byType = all.data?.by_type ?? []

  const active = scope === 'all' ? all : unattributed
  const rows = active.data?.artifacts ?? []

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Archive className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Artifacts</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          What has already been built on the platform. The unattributed part is the point: work
          the portfolio doesn't know about, or something abandoned that still costs money.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Artifacts', value: summary.total ?? '—' },
            { label: 'Unclaimed', value: summary.unattributed ?? '—' },
            { label: 'Active + unclaimed', value: unclaimedSummary.active_unclaimed ?? '—' },
            { label: 'Types', value: summary.types ?? '—' },
          ]}
        />
      </div>

      {unclaimedSummary.active_unclaimed ? (
        <Banner kind="warn">
          {`${unclaimedSummary.active_unclaimed} artifact(s) ran recently but no use case ` +
            'claims them — likely shadow work worth adding to the portfolio.'}
        </Banner>
      ) : null}

      {byType.length ? (
        <div className="card p-0">
          <h3 className="text-sm font-semibold text-white px-4 pt-4 pb-2">By type</h3>
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">Type</th>
                <th className="text-right px-4 py-2.5 font-medium">Total</th>
                <th className="text-right px-4 py-2.5 font-medium">Unclaimed</th>
              </tr>
            </thead>
            <tbody>
              {byType.map((entry) => (
                <tr key={entry.artifact_type} className="border-b border-navy-600">
                  <td className="px-4 py-2 text-navy-300">{entry.artifact_type}</td>
                  <td className="px-4 py-2 text-right font-mono text-navy-300">{entry.n}</td>
                  <td className="px-4 py-2 text-right font-mono text-warning-400">
                    {entry.unattributed}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <button
          aria-pressed={scope === 'all'}
          onClick={() => setScope('all')}
          className={`${scope === 'all' ? 'btn-primary' : 'btn-secondary'} text-sm`}
        >
          Everything built
        </button>
        <button
          aria-pressed={scope === 'unclaimed'}
          onClick={() => setScope('unclaimed')}
          className={`${scope === 'unclaimed' ? 'btn-primary' : 'btn-secondary'} text-sm`}
        >
          Unclaimed only
        </button>
        {scope === 'unclaimed' && unattributed.data?.interpretation ? (
          <span className="text-xs text-navy-500 max-w-[52ch]">
            {unattributed.data.interpretation}
          </span>
        ) : null}
      </div>

      <QueryState
        query={active}
        loading="Loading artifacts…"
        empty={!rows.length}
        emptyMessage={
          scope === 'unclaimed'
            ? 'Every artifact is claimed by a use case.'
            : 'Nothing scanned yet. Run a workspace scan from the console to populate this.'
        }
      />

      {rows.length ? (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm min-w-[880px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Artifact</th>
                <th className="text-left px-3 py-2.5 font-medium">Type</th>
                <th className="text-left px-3 py-2.5 font-medium">Owner</th>
                <th className="text-left px-3 py-2.5 font-medium">Activity</th>
                <th className="text-left px-3 py-2.5 font-medium">Claimed by</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((artifact) => (
                <tr key={artifact.id} className="border-b border-navy-600 hover:bg-lava/5">
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-white">{artifact.name}</div>
                    <div className="text-xs text-navy-500 font-mono">{artifact.artifact_id}</div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-navy-400">{artifact.artifact_type}</td>
                  <td className="px-3 py-2.5 text-xs text-navy-400">{artifact.owner ?? '—'}</td>
                  <td className="px-3 py-2.5 text-xs text-navy-400">{ranLabel(artifact)}</td>
                  <td className="px-3 py-2.5 text-xs">
                    {artifact.use_case_title ? (
                      <>
                        <div className="text-navy-300">{artifact.use_case_title}</div>
                        {artifact.lob_name ? (
                          <div className="text-navy-500">{artifact.lob_name}</div>
                        ) : null}
                      </>
                    ) : (
                      <span className="badge-high">unclaimed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
