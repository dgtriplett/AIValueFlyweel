// One filter state shared by Portfolio, Use Case Catalog and Data Assets.
//
// It lives above the views rather than inside each one so that switching tabs
// keeps your filters — you narrow to "Distribution, shovel-ready", then flip
// between the portfolio and the sources serving it without retyping.

import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { DataAsset, UseCase } from '../types'

export interface Filters {
  q: string
  lobId: number | null
  subVertical: string | null
  phase: number | null
  status: string | null
  ingestion: string | null
  readiness: string | null
}

const EMPTY: Filters = {
  q: '',
  lobId: null,
  subVertical: null,
  phase: null,
  status: null,
  ingestion: null,
  readiness: null,
}

interface FilterContextValue {
  filters: Filters
  set: (partial: Partial<Filters>) => void
  reset: () => void
  active: boolean
}

const FilterContext = createContext<FilterContextValue | null>(null)

export function FilterProvider({ children }: { children: ReactNode }) {
  const [filters, setFilters] = useState<Filters>(EMPTY)

  const set = useCallback((partial: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...partial }))
  }, [])

  const reset = useCallback(() => setFilters(EMPTY), [])

  const active = useMemo(
    () => Object.keys(EMPTY).some((key) => filters[key as keyof Filters] !== EMPTY[key as keyof Filters]),
    [filters],
  )

  const value = useMemo(() => ({ filters, set, reset, active }), [filters, set, reset, active])
  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

export function useFilters(): FilterContextValue {
  const context = useContext(FilterContext)
  if (!context) throw new Error('useFilters must be used inside FilterProvider')
  return context
}

/** Free-text search covers the tags too, so "NERC" finds compliance-driven work. */
export function matchesUseCase(useCase: UseCase, filters: Filters): boolean {
  if (filters.lobId != null && useCase.lob_id !== filters.lobId) return false
  if (filters.subVertical && useCase.sub_vertical !== filters.subVertical) return false
  if (filters.phase != null && useCase.phase !== filters.phase) return false
  if (filters.status && useCase.status !== filters.status) return false
  if (filters.readiness && useCase.readiness !== filters.readiness) return false
  if (filters.q) {
    const haystack = [
      useCase.title ?? '',
      useCase.description ?? '',
      (useCase.risk_tags ?? []).join(' '),
      (useCase.compliance_tags ?? []).join(' '),
    ]
      .join(' ')
      .toLowerCase()
    if (!haystack.includes(filters.q.toLowerCase())) return false
  }
  return true
}

export function matchesDataAsset(asset: DataAsset, filters: Filters): boolean {
  if (filters.lobId != null) {
    const benefiting = asset.benefiting_lob_ids ?? []
    if (asset.owning_lob_id !== filters.lobId && !benefiting.includes(filters.lobId)) {
      return false
    }
  }
  if (filters.subVertical && asset.sub_vertical !== filters.subVertical) return false
  if (filters.ingestion && asset.ingestion_status !== filters.ingestion) return false
  if (filters.q) {
    const haystack =
      `${asset.source_system ?? ''} ${asset.module ?? ''} ${asset.description ?? ''}`.toLowerCase()
    if (!haystack.includes(filters.q.toLowerCase())) return false
  }
  return true
}
