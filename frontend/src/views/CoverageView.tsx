// Coverage & gaps: every data need against every line of business.
//
// Ported from `console.js:603-665` (`viewCoverage`), with the console's separate
// "Data domains" view (`:725-784`) and its duplicate catalog sub-tab (`:1337-1359`)
// folded in as this view's second section. TIER3_MIGRATION_PLAN.md §3 calls for
// exactly that fold: the two were the same data at two granularities — the matrix
// answers "where are we exposed", the catalog answers "what is the need called and
// does anything serve it" — and splitting them across two destinations meant
// clicking away to answer the obvious follow-up question.
//
// THE CELL STATE IS THE VIEW
// --------------------------
// Four states, and the one worth building a screen for is `available`: data you
// have already landed that no use case in that LOB asks for. Nothing else in the
// app surfaces it, and it is the cheapest place to look for a new use case
// because the ingestion cost is already sunk. `covered` and `gap` are the
// expected pair; `unused` is the quiet majority and is styled to recede.
//
// Endpoints: GET /domains/coverage-matrix, GET /domains, GET /domains/gaps?limit=15.
// All three are reads with no rate limit. No writes — the console had none here
// either, so this is full parity rather than a reduced surface.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Table2 } from 'lucide-react'
import type { ReactNode } from 'react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { DomainsTable, landedLabel } from '../components/DomainsTable'
import { StatStrip } from '../components/StatStrip'
import type { CoverageCell, CoverageRow } from '../types'

/** The console's four swatch classes, as Tailwind-era inline styles.
 *
 *  Inline rather than utility classes because these are alpha composites over the
 *  row-hover tint and the palette has no token for "30% success" — the same
 *  reason `index.css`'s badges carry literal rgba. */
const CELL_STYLES: Record<CoverageCell['state'], { background: string; color: string }> = {
  covered: { background: 'rgba(0,169,114,0.30)', color: '#9ED6C4' },
  gap: { background: 'rgba(255,54,33,0.28)', color: '#FF9E94' },
  available: { background: 'rgba(34,114,180,0.28)', color: '#8ACAFF' },
  unused: { background: '#143D4A', color: '#618794' },
}

const LEGEND: { state: CoverageCell['state']; label: string }[] = [
  { state: 'covered', label: 'Covered — needed and landed' },
  { state: 'gap', label: 'Gap — needed, not landed' },
  { state: 'available', label: 'Have it, nothing uses it' },
  { state: 'unused', label: 'Not needed here' },
]

type Section = 'matrix' | 'needs'

/** The cell's own explanation, which in the console was its `title` attribute.
 *  Kept as a tooltip: 60 cells cannot each carry visible prose, but a cell that
 *  cannot say what it means is a coloured square. */
function cellTooltip(row: CoverageRow, cell: CoverageCell): string {
  const head = `${row.domain.label} × ${cell.lob_name} — ${cell.state}`
  if (!cell.use_case_count) return head
  return `${head}; ${cell.use_case_count} use case(s), $${cell.value_mm}M`
}

export default function CoverageView() {
  const [section, setSection] = useState<Section>('matrix')

  const matrix = useQuery({ queryKey: ['coverage-matrix'], queryFn: api.coverageMatrix })
  // The needs catalog and its ranked gaps. Both are cheap and the section
  // switch is instant either way, so they are not deferred behind the tab —
  // react-query dedupes and the second visit is already warm.
  const domains = useQuery({ queryKey: ['domains'], queryFn: api.domains })
  const gaps = useQuery({ queryKey: ['domain-gaps'], queryFn: () => api.domainGaps(15) })

  const lobs = matrix.data?.lobs ?? []
  const rows = matrix.data?.rows ?? []
  const summary = matrix.data?.summary ?? {}

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Coverage &amp; gaps</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          Every data need against every line of business. Numbers are the use cases in that
          cell; colour is whether the need is met. The needs catalog behind it names each need
          and shows what serves it.
        </p>
      </div>

      <div className="flex gap-2">
        <SectionButton active={section === 'matrix'} onClick={() => setSection('matrix')}>
          <BarChart3 className="w-4 h-4" /> Coverage matrix
        </SectionButton>
        <SectionButton active={section === 'needs'} onClick={() => setSection('needs')}>
          <Table2 className="w-4 h-4" /> Data needs
        </SectionButton>
      </div>

      {section === 'matrix' ? (
        <div className="space-y-4">
          <div className="card">
            <StatStrip
              stats={[
                { label: 'Covered', value: summary.covered ?? '—' },
                { label: 'Gaps', value: summary.gaps ?? '—' },
                { label: 'Have but unused', value: summary.available_unused ?? '—' },
                { label: 'Universal gaps', value: summary.universal_gaps ?? '—' },
                {
                  label: 'Value at risk',
                  value: `$${summary.value_at_risk_mm ?? 0}M`,
                },
              ]}
            />
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-navy-400">
            {LEGEND.map((entry) => (
              <span key={entry.state} className="inline-flex items-center gap-1.5">
                <span
                  className="w-3 h-3 rounded-[3px] inline-block"
                  style={{ background: CELL_STYLES[entry.state].background }}
                />
                {entry.label}
              </span>
            ))}
          </div>

          {summary.available_unused ? (
            <Banner kind="info">
              {`${summary.available_unused} cell(s) are data you have already landed that no ` +
                'use case in that line of business asks for — the cheapest place to look for a ' +
                'new use case, since the data is there.'}
            </Banner>
          ) : null}
          {summary.universal_gaps ? (
            <Banner kind="err">
              {`${summary.universal_gaps} data need(s) are unmet in EVERY line of business ` +
                'that requires them. Nothing in the estate provides these, so they are ' +
                'acquisition decisions rather than ingestion backlog.'}
            </Banner>
          ) : null}

          <QueryState
            query={matrix}
            loading="Building the matrix…"
            empty={!lobs.length}
            emptyMessage="No lines of business defined yet."
          />

          {lobs.length ? (
            <div className="card p-0 overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-b border-navy-600">
                  <tr>
                    <th className="text-left px-3 py-2.5 font-medium text-navy-500 uppercase min-w-[230px]">
                      Data need
                    </th>
                    {lobs.map((lob) => (
                      // Vertical headers: six-plus LOB names side by side is the
                      // only way this table fits a laptop without a scrollbar
                      // wider than the screen.
                      <th
                        key={lob.id}
                        className="px-1.5 py-1.5 h-24 font-medium text-navy-400 whitespace-nowrap align-bottom"
                        style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                      >
                        {lob.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.domain.id} className="border-b border-navy-600">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-white text-sm">
                            {row.domain.label}
                          </span>
                          {row.universal_gap ? (
                            <span className="badge-critical">universal gap</span>
                          ) : null}
                        </div>
                        <div className="text-[11px] text-navy-500 font-mono">
                          {`${row.domain.category ?? ''} · ${landedLabel({
                            ...row.domain,
                            serving_asset_count: row.serving_asset_count,
                            ready_asset_count: row.ready_asset_count,
                          })}`}
                        </div>
                      </td>
                      {row.cells.map((cell) => (
                        <td key={cell.lob_id} className="p-1 text-center">
                          <span
                            className="block h-5 leading-5 rounded-[3px] font-mono text-[10px]"
                            style={CELL_STYLES[cell.state]}
                            title={cellTooltip(row, cell)}
                          >
                            {cell.use_case_count || ''}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="card">
            <p className="text-sm text-navy-400 max-w-[70ch]">
              Semantic data needs, decoupled from the products that provide them. A use case
              needing <em>work order history</em> is satisfied by any system that supplies it —
              so running Maximo instead of SAP PM no longer shows as a gap.
            </p>
            <div className="mt-4">
              <StatStrip
                stats={[
                  { label: 'Needs', value: domains.data?.length ?? '—' },
                  {
                    label: 'Satisfied',
                    value: domains.data
                      ? domains.data.filter((domain) => domain.satisfied).length
                      : '—',
                  },
                  {
                    label: 'Gaps',
                    value: domains.data
                      ? domains.data.filter((domain) => !domain.satisfied).length
                      : '—',
                  },
                  {
                    label: 'Value blocked',
                    value: `$${gaps.data?.summary?.total_value_blocked_mm ?? 0}M`,
                  },
                ]}
              />
            </div>
          </div>

          {(gaps.data?.gaps ?? []).length ? (
            <div className="card p-0">
              <h3 className="text-sm font-semibold text-white px-4 pt-4 pb-2">
                Gaps ranked by value blocked
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[760px]">
                  <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
                    <tr>
                      <th className="text-left px-3 py-2.5 font-medium">Data need</th>
                      <th className="text-left px-3 py-2.5 font-medium">Category</th>
                      <th className="text-right px-3 py-2.5 font-medium">Use cases</th>
                      <th className="text-right px-3 py-2.5 font-medium">Value blocked</th>
                      <th className="text-left px-3 py-2.5 font-medium">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(gaps.data?.gaps ?? []).map((gap) => (
                      <tr key={gap.domain.id} className="border-b border-navy-600 hover:bg-lava/5">
                        <td className="px-3 py-2.5 font-medium text-white">{gap.domain.label}</td>
                        <td className="px-3 py-2.5 text-xs text-navy-400">
                          {gap.domain.category ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                          {gap.blocked_use_case_count}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-lava-300">
                          {`$${gap.value_blocked_mm}M`}
                        </td>
                        <td className="px-3 py-2.5 text-xs text-navy-400">{gap.rationale}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          <QueryState
            query={domains}
            loading="Loading data needs…"
            empty={!(domains.data ?? []).length}
            emptyMessage="No data needs defined yet."
          />

          {(domains.data ?? []).length ? (
            <div className="card p-0">
              <h3 className="text-sm font-semibold text-white px-4 pt-4 pb-2">All data needs</h3>
              <DomainsTable domains={domains.data ?? []} />
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function SectionButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`${active ? 'btn-primary' : 'btn-secondary'} text-sm`}
    >
      {children}
    </button>
  )
}
