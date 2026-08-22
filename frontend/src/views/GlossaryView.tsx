// Glossary: business terms with the systems behind them.
//
// Ported from `console.js:1439-1458` (`subGlossary`, the catalog's Glossary
// sub-tab). Read-only in the console too — there was no create or edit control —
// so this is a full-parity port rather than a reduced surface.
//
// WHY `origin_kind` IS THE COLUMN THAT MATTERS
// -------------------------------------------
// Terms arrive two ways. Curated ones somebody wrote. Derived ones are synthesized
// from the data needs, on the observation that a need already IS a term with a
// definition — so the glossary is populated on day one instead of being an empty
// table nobody fills in. But a derived definition is a restatement of a need, not
// an agreed business definition, so it is marked and stays marked until someone
// curates it. Hiding that distinction would present machine text as an
// organization's agreed vocabulary.
//
// The console truncated definitions to 110 characters and showed the first three
// source systems. Both survive as CSS/slice rather than as string surgery, and
// the full text is in the row's `title` — the truncation was a table-layout
// decision, not a decision that the rest of the definition does not matter.
//
// Endpoint: GET /flow/glossary. One read, no rate limit.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, Search } from 'lucide-react'

import { api } from '../api'
import { QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import type { GlossaryTerm } from '../types'

type OriginFilter = 'all' | 'curated' | 'derived'

/** How many rows to render at once. The console hard-capped at 80 with no way to
 *  see the 81st; this pages instead, so a large glossary is reachable. */
const PAGE = 80

function matches(term: GlossaryTerm, query: string): boolean {
  if (!query) return true
  const needle = query.toLowerCase()
  return (
    term.term.toLowerCase().includes(needle) ||
    (term.definition ?? '').toLowerCase().includes(needle) ||
    (term.source_systems ?? []).some((system) => system.toLowerCase().includes(needle))
  )
}

export default function GlossaryView() {
  const [query, setQuery] = useState('')
  const [origin, setOrigin] = useState<OriginFilter>('all')
  const [limit, setLimit] = useState(PAGE)

  const glossary = useQuery({ queryKey: ['glossary'], queryFn: api.glossary })

  const summary = glossary.data?.summary ?? {}
  const all = glossary.data?.terms ?? []
  const filtered = all.filter(
    (term) => (origin === 'all' || term.origin_kind === origin) && matches(term, query),
  )
  const rows = filtered.slice(0, limit)

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <BookOpen className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Glossary</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[70ch]">
          Business terms with the systems behind them. Data needs appear automatically — a need
          already is a term with a definition — and are marked <em>derived</em> until someone
          curates one.
        </p>
      </div>

      <div className="card">
        <StatStrip
          stats={[
            { label: 'Terms', value: summary.total ?? '—' },
            { label: 'Curated', value: summary.curated ?? '—' },
            { label: 'Derived', value: summary.derived ?? '—' },
          ]}
        />
      </div>

      <div className="card flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 text-navy-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            id="glossary-search"
            name="glossary-search"
            aria-label="Search terms, definitions and source systems"
            className="input-field pl-8"
            placeholder="Search terms, definitions, systems…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setLimit(PAGE)
            }}
          />
        </div>
        <div className="flex gap-1.5">
          {(['all', 'curated', 'derived'] as OriginFilter[]).map((option) => (
            <button
              key={option}
              aria-pressed={origin === option}
              onClick={() => {
                setOrigin(option)
                setLimit(PAGE)
              }}
              className={`${origin === option ? 'btn-primary' : 'btn-secondary'} text-sm capitalize`}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      <QueryState
        query={glossary}
        loading="Loading the glossary…"
        empty={!all.length}
        emptyMessage="No terms yet. Data needs become derived terms as soon as they exist."
      />

      {all.length && !filtered.length ? (
        <div className="card text-center text-sm text-navy-500 py-8">
          No terms match that search.
        </div>
      ) : null}

      {rows.length ? (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm min-w-[820px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Term</th>
                <th className="text-left px-3 py-2.5 font-medium">Definition</th>
                <th className="text-left px-3 py-2.5 font-medium">Systems of record</th>
                <th className="text-left px-3 py-2.5 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((term) => (
                <tr
                  // Derived terms have no row id of their own, so the term text is
                  // the key. It is the glossary's natural key either way.
                  key={`${term.origin_kind}:${term.id ?? term.term}`}
                  className="border-b border-navy-600 hover:bg-lava/5"
                >
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-white">{term.term}</div>
                    {term.domain_label ? (
                      <div className="text-xs text-navy-500">{term.domain_label}</div>
                    ) : null}
                  </td>
                  {/* Clamped in CSS, full text in the tooltip: the truncation is a
                      layout decision, so it does not need to destroy the string. */}
                  <td
                    className="px-3 py-2.5 text-xs text-navy-400 max-w-[420px]"
                    title={term.definition ?? undefined}
                  >
                    <span className="line-clamp-2">{term.definition ?? '—'}</span>
                  </td>
                  <td className="px-3 py-2.5 text-xs font-mono text-navy-400">
                    {(term.source_systems ?? []).slice(0, 3).join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={term.origin_kind === 'curated' ? 'badge-low' : 'badge-high'}>
                      {term.origin_kind}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > rows.length ? (
            <div className="px-4 py-3 flex items-center gap-3">
              <button className="btn-secondary text-sm" onClick={() => setLimit(limit + PAGE)}>
                Show more
              </button>
              <span className="text-xs text-navy-500">
                {`${rows.length} of ${filtered.length} shown`}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
