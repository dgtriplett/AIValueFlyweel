// The one filter row shared by Portfolio, Use Case Catalog and Data Assets.
//
// Which controls appear is driven by `showIngestion` rather than by the tab id, so
// the bar does not need to know what it is mounted under: the registry filters
// data assets (ingestion status, no project status or readiness) and the use-case
// views filter use cases.
//
// There is deliberately no phase control. Phase is derived from prerequisite depth
// and filtering by it invites customers to read it as a delivery plan; readiness is
// the question they actually mean. See
// `tests/test_console_nav.py::test_phase_is_not_customer_visible_in_the_spa`.

import { Search, X, Zap } from 'lucide-react'

import {
  INGESTION,
  INGESTION_LABELS,
  READINESS,
  READINESS_LABELS,
  SELECT_CLASS,
  STATUSES,
  STATUS_LABELS,
  SUB_VERTICALS,
  SUB_VERTICAL_LABELS,
} from '../constants'
import { useFilters } from '../context/FilterContext'
import type { Lob } from '../types'

export function FilterBar({
  lobs,
  showIngestion,
}: {
  lobs: Lob[]
  showIngestion?: boolean
}) {
  const { filters, set, reset, active } = useFilters()

  return (
    <div className="card p-3 flex flex-wrap items-center gap-2">
      <div className="relative flex-1 min-w-[220px]">
        <Search className="w-4 h-4 text-navy-500 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          id="filter-search"
          name="filter-search"
          aria-label="Search use cases and data assets"
          className="input-field pl-9"
          placeholder="Search use cases and data assets…"
          value={filters.q}
          onChange={(event) => set({ q: event.target.value })}
        />
      </div>

      <select
        id="filter-lob"
        name="filter-lob"
        aria-label="Filter by domain (LOB)"
        className={SELECT_CLASS}
        value={filters.lobId ?? ''}
        onChange={(event) => set({ lobId: event.target.value ? Number(event.target.value) : null })}
      >
        <option value="">All domains (LOB)</option>
        {lobs.map((lob) => (
          <option key={lob.id} value={lob.id}>
            {lob.name}
          </option>
        ))}
      </select>

      {!showIngestion ? (
        <>
          <select
            id="filter-status"
            name="filter-status"
            aria-label="Filter by project status"
            className={SELECT_CLASS}
            value={filters.status ?? ''}
            onChange={(event) => set({ status: event.target.value || null })}
          >
            <option value="">All statuses</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>

          <select
            id="filter-readiness"
            name="filter-readiness"
            aria-label="Filter by readiness"
            className={SELECT_CLASS}
            value={filters.readiness ?? ''}
            onChange={(event) => set({ readiness: event.target.value || null })}
          >
            <option value="">All readiness</option>
            {READINESS.map((readiness) => (
              <option key={readiness} value={readiness}>
                {READINESS_LABELS[readiness]}
              </option>
            ))}
          </select>
        </>
      ) : null}

      <select
        id="filter-subvertical"
        name="filter-subvertical"
        aria-label="Filter by generation type"
        className={SELECT_CLASS}
        value={filters.subVertical ?? ''}
        onChange={(event) => set({ subVertical: event.target.value || null })}
      >
        <option value="">All generation types</option>
        {SUB_VERTICALS.map((subVertical) => (
          <option key={subVertical} value={subVertical}>
            {SUB_VERTICAL_LABELS[subVertical]}
          </option>
        ))}
      </select>

      {showIngestion ? (
        <select
          id="filter-ingestion"
          name="filter-ingestion"
          aria-label="Filter by ingestion status"
          className={SELECT_CLASS}
          value={filters.ingestion ?? ''}
          onChange={(event) => set({ ingestion: event.target.value || null })}
        >
          <option value="">All ingestion</option>
          {INGESTION.map((ingestion) => (
            <option key={ingestion} value={ingestion}>
              {INGESTION_LABELS[ingestion]}
            </option>
          ))}
        </select>
      ) : null}

      {/* A one-click shortcut for the readiness select's most-used value. */}
      {!showIngestion ? (
        <button
          className={`text-sm px-3 py-1.5 rounded border flex items-center gap-1.5 transition-colors ${
            filters.readiness === 'shovel_ready'
              ? 'bg-success/20 border-success/60 text-success'
              : 'bg-navy-700 border-navy-600 text-navy-300 hover:border-success/50'
          }`}
          onClick={() =>
            set({ readiness: filters.readiness === 'shovel_ready' ? null : 'shovel_ready' })
          }
          title="Show only use cases whose required data is ready"
        >
          <Zap className="w-3.5 h-3.5" /> Shovel-ready now
        </button>
      ) : null}

      {active ? (
        <button
          className="text-sm px-2.5 py-1.5 rounded bg-navy-700 border border-navy-600 text-navy-400 hover:text-white flex items-center gap-1"
          onClick={reset}
        >
          <X className="w-3.5 h-3.5" /> Clear
        </button>
      ) : null}
    </div>
  )
}
