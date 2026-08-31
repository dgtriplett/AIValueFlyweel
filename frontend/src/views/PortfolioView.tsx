// Use cases: the ones this customer has committed to, and the catalog to draw
// from — one destination, two scopes.
//
// The catalog used to be a second top-level tab even though it reads the same
// `/api/use-cases` endpoint and differs only by `?scope=`. That made a data
// filter look like a destination. The scope switch lives here at the top; the
// catalog panel is still its own lazy chunk (it pulls its own recommender query)
// and is rendered instead of the portfolio body when that scope is picked.
//
// Table and Kanban are the same rows read two ways — the table is for triage
// ("what is worth the most, what is blocked"), the board is for a standup. The
// list is owned by the shell (it feeds the KPI strip too), so `useCases`,
// `loading` and `error` arrive as props; the two mutations that write a row live
// here because only this view offers them.

import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowDownUp, BookOpen, Plus, SquareKanban, Table, Trash2 } from 'lucide-react'

import { api } from '../api'
import {
  KANBAN_PHASES,
  LOB_COLORS,
  PHASE_COLORS,
  PHASE_LABELS,
  READINESS_COLORS,
  STATUSES,
  STATUS_COLORS,
  STATUS_LABELS,
  fmtMoney,
} from '../constants'
import { ReadinessBadge } from '../components/Badges'
import { RecommendPanel } from '../components/RecommendPanel'
import { ScopeSwitch } from '../components/ScopeSwitch'
import type { UseCaseView } from '../components/ScopeSwitch'
import { SourceRecommendPanel } from '../components/SourceRecommendPanel'
import { AiRecommendationsPanel } from '../components/AiRecommendationsPanel'
import { KANBAN_ENABLED_KEY, readBoolPref, writeBoolPref } from '../lib/prefs'
import { matchesUseCase, useFilters } from '../context/FilterContext'
import { useReadOnly } from '../context/RoleContext'
import type { Lob, Readiness, Status, UseCase } from '../types'

// The catalog panel carries its own recommender query and table, and most
// sessions never switch scope — so it stays a chunk that loads on demand.
const CatalogView = lazy(() => import('./CatalogView'))

/** Sort keys are read off the row, so they mirror field names where one exists. */
type SortKey = 'title' | 'status' | 'readiness' | 'priority_score' | 'computed_value' | 'realized'

/** Readiness and status are ordinal, not alphabetical — a table sort has to know that. */
const READINESS_RANK: Record<Readiness, number> = {
  shovel_ready: 4,
  awaiting_prerequisites: 3,
  nearly_ready: 2,
  blocked: 1,
}
const STATUS_RANK: Record<Status, number> = {
  not_started: 1,
  scoping: 2,
  in_progress: 3,
  live: 4,
  value_realized: 5,
}

interface Column {
  key: string
  label: string
  color: string
  match: (useCase: UseCase) => boolean
}

export function PortfolioView({
  useCases = [],
  lobs = [],
  onOpen,
  onNew,
  scope = 'portfolio',
  onScope,
  loading = false,
  error = false,
}: {
  useCases?: UseCase[]
  lobs?: Lob[]
  onOpen?: (useCaseId: number) => void
  onNew?: () => void
  /** Which scope is on screen. Owned by the shell so it survives a tab round-trip. */
  scope?: UseCaseView
  onScope?: (scope: UseCaseView) => void
  loading?: boolean
  error?: boolean
}) {
  const queryClient = useQueryClient()
  const { filters } = useFilters()
  const readOnly = useReadOnly()
  const [view, setView] = useState<'table' | 'kanban'>('table')
  // Kanban is offered by default; a local preference can turn it off. When it
  // is off the toggle hides the Kanban option and the view is forced to 'table'.
  const [kanbanEnabled, setKanbanEnabled] = useState(() => readBoolPref(KANBAN_ENABLED_KEY, true))
  const [grouping, setGrouping] = useState<'status' | 'phase'>('status')
  const [sort, setSort] = useState<{ key: SortKey; dir: number }>({
    key: 'computed_value',
    dir: -1,
  })
  const [sorted, setSorted] = useState(false)

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteUseCase(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['use-cases'] }),
  })
  // Advancing a use case can flip an asset's ingestion status server-side, so the
  // data-asset list is stale the moment a status lands.
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.setStatus(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
    },
  })

  // Disabling Kanban while the board is on screen must not strand the user on a
  // view they can no longer switch away from — fall back to the table.
  useEffect(() => {
    if (!kanbanEnabled && view === 'kanban') setView('table')
  }, [kanbanEnabled, view])

  const setKanbanPref = (next: boolean) => {
    setKanbanEnabled(next)
    writeBoolPref(KANBAN_ENABLED_KEY, next)
    if (!next) setView('table')
  }

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '—') : '—'

  const visible = useMemo(
    () => useCases.filter((useCase) => matchesUseCase(useCase, filters)),
    [useCases, filters],
  )

  const realizedOf = (useCase: UseCase) => useCase.realized?.value ?? 0

  // Until a header is clicked, delivered value outranks hypothesized value: a
  // customer opening this view wants to see what has actually paid off, first.
  const rows = useMemo(() => {
    return [...visible].sort((left, right) => {
      if (!sorted) {
        const leftHasRealized = realizedOf(left) > 0 ? 1 : 0
        const rightHasRealized = realizedOf(right) > 0 ? 1 : 0
        if (leftHasRealized !== rightHasRealized) return rightHasRealized - leftHasRealized
        const byRealized = realizedOf(right) - realizedOf(left)
        if (byRealized !== 0) return byRealized
        return (right.computed_value ?? 0) - (left.computed_value ?? 0)
      }
      let a: string | number = 0
      let b: string | number = 0
      if (sort.key === 'readiness') {
        a = left.readiness ? READINESS_RANK[left.readiness] : 0
        b = right.readiness ? READINESS_RANK[right.readiness] : 0
      } else if (sort.key === 'status') {
        a = left.status ? STATUS_RANK[left.status] : 0
        b = right.status ? STATUS_RANK[right.status] : 0
      } else if (sort.key === 'title') {
        a = left.title
        b = right.title
      } else if (sort.key === 'realized') {
        a = realizedOf(left)
        b = realizedOf(right)
      } else {
        a = left[sort.key] ?? 0
        b = right[sort.key] ?? 0
      }
      if (a < b) return -1 * sort.dir
      if (a > b) return 1 * sort.dir
      return 0
    })
  }, [visible, sorted, sort])

  const applySort = (key: SortKey) => {
    setSorted(true)
    setSort((current) => (current.key === key ? { key, dir: current.dir * -1 } : { key, dir: -1 }))
  }

  const columns: Column[] =
    grouping === 'status'
      ? STATUSES.map((status) => ({
          key: status,
          label: STATUS_LABELS[status],
          color: STATUS_COLORS[status],
          match: (useCase: UseCase) => useCase.status === status,
        }))
      : KANBAN_PHASES.map((phase) => ({
          key: String(phase),
          label: `P${phase} · ${PHASE_LABELS[phase]}`,
          color: PHASE_COLORS[phase],
          match: (useCase: UseCase) => useCase.phase === phase,
        }))

  const switcher = <ScopeSwitch scope={scope} setScope={(next) => onScope?.(next)} />

  // The catalog is the same list at a different scope, so it renders under the
  // same switch rather than as its own destination. Filters are shared state, so
  // a narrowing set on one scope still holds when you flip to the other.
  if (scope === 'catalog') {
    return (
      <div className="space-y-4">
        {switcher}
        <Suspense fallback={<div className="text-navy-400 py-8 text-center">Loading catalog…</div>}>
          <CatalogView lobs={lobs} onOpen={onOpen} />
        </Suspense>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {switcher}
      {/* AI recommendations are collapsed on landing so they don't dominate the
          view — the user flagged them as "a lot for the initial landing view".
          The feature is one click away; the choice to keep it open persists. */}
      <AiRecommendationsPanel count={2}>
        <RecommendPanel onOpen={onOpen} />
        <SourceRecommendPanel onOpenUseCase={onOpen} />
      </AiRecommendationsPanel>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex">
            <button
              className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
                view === 'table' ? 'bg-lava/20 text-lava-300' : 'text-navy-400'
              }`}
              onClick={() => setView('table')}
            >
              <Table className="w-4 h-4" /> Table
            </button>
            {/* The Kanban option only appears when the preference has it on.
                When it is off, the toggle is a single (table) state, which is
                clearer than a disabled-looking second button. */}
            {kanbanEnabled && (
              <button
                className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
                  view === 'kanban' ? 'bg-lava/20 text-lava-300' : 'text-navy-400'
                }`}
                onClick={() => setView('kanban')}
              >
                <SquareKanban className="w-4 h-4" /> Kanban
              </button>
            )}
          </div>
          {kanbanEnabled && view === 'kanban' && (
            <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex text-xs">
              <button
                className={`px-2.5 py-1.5 rounded ${
                  grouping === 'status' ? 'bg-info/20 text-info' : 'text-navy-400'
                }`}
                onClick={() => setGrouping('status')}
              >
                by Status
              </button>
              <button
                className={`px-2.5 py-1.5 rounded ${
                  grouping === 'phase' ? 'bg-info/20 text-info' : 'text-navy-400'
                }`}
                onClick={() => setGrouping('phase')}
              >
                by Phase
              </button>
            </div>
          )}
          <span className="text-sm text-navy-500">{visible.length} use cases</span>
          {/* A small, discoverable control to turn the Kanban board on or off.
              Kept in the options row rather than an admin panel — this is a
              per-browser preference (gridatlas.kanbanEnabled), not an org setting. */}
          <label className="flex items-center gap-1.5 text-xs text-navy-400 cursor-pointer select-none">
            <input
              type="checkbox"
              className="accent-lava"
              checked={kanbanEnabled}
              onChange={(event) => setKanbanPref(event.target.checked)}
            />
            Show Kanban board
          </label>
        </div>
        {!readOnly && (
          <button className="btn-primary text-sm" onClick={() => onNew?.()}>
            <Plus className="w-4 h-4" /> New Use Case
          </button>
        )}
      </div>

      {loading && <div className="text-navy-400">Loading…</div>}
      {error && <div className="text-lava-300 text-sm">Couldn't load use cases. Please retry.</div>}

      {!loading &&
        !error &&
        visible.length === 0 &&
        (useCases.length === 0 ? (
          <div className="card text-center py-10 px-6">
            <BookOpen className="w-8 h-8 text-navy-500 mx-auto mb-3" />
            <div className="text-white font-medium">Your portfolio is empty</div>
            <div className="text-navy-400 text-sm mt-1 max-w-md mx-auto">
              Switch to the <span className="text-lava-300">Catalog</span> to bring in predefined
              ideas
              {!readOnly && (
                <>
                  , or add your own with <span className="text-lava-300">New use case</span>
                </>
              )}
              .
            </div>
            <div className="flex items-center justify-center gap-2 mt-4">
              {onScope && (
                <button
                  className="btn-primary text-sm flex items-center gap-1.5"
                  onClick={() => onScope('catalog')}
                >
                  <BookOpen className="w-4 h-4" /> Browse the catalog
                </button>
              )}
              {!readOnly && (
                <button
                  className="btn-secondary text-sm flex items-center gap-1.5"
                  onClick={() => onNew?.()}
                >
                  <Plus className="w-4 h-4" /> Add your own
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="card text-center text-navy-500 text-sm py-8">
            No use cases match the current filters.
          </div>
        ))}

      {view === 'table' || !kanbanEnabled ? (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm min-w-[960px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                <SortableHeader onClick={() => applySort('title')}>Use case</SortableHeader>
                <th className="text-left px-3 py-2.5 font-medium">Domain</th>
                <SortableHeader onClick={() => applySort('status')}>Status</SortableHeader>
                <SortableHeader onClick={() => applySort('readiness')}>Readiness</SortableHeader>
                <SortableHeader onClick={() => applySort('priority_score')}>Priority</SortableHeader>
                <SortableHeader onClick={() => applySort('computed_value')}>
                  Hyp. value
                </SortableHeader>
                <th className="text-left px-3 py-2.5 font-medium">Realized</th>
                {!readOnly && <th className="px-3 py-2.5" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((useCase) => (
                <tr
                  key={useCase.id}
                  className="border-b border-navy-600 hover:bg-lava/5 cursor-pointer"
                  onClick={() => onOpen?.(useCase.id)}
                >
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-white">{useCase.title}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-1.5 text-navy-300">
                      <span
                        className="w-2 h-2 rounded-full"
                        style={{ background: LOB_COLORS[lobName(useCase.lob_id)] ?? '#618794' }}
                      />
                      {lobName(useCase.lob_id)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5" onClick={(event) => event.stopPropagation()}>
                    {readOnly ? (
                      <span
                        className="text-xs font-semibold rounded-full px-2 py-1 border"
                        style={{
                          color: STATUS_COLORS[useCase.status ?? 'not_started'],
                          borderColor: `${STATUS_COLORS[useCase.status ?? 'not_started']}66`,
                        }}
                      >
                        {STATUS_LABELS[useCase.status ?? 'not_started']}
                      </span>
                    ) : (
                      <select
                        id={`status-${useCase.id}`}
                        name={`status-${useCase.id}`}
                        aria-label={`Status for ${useCase.title}`}
                        value={useCase.status ?? 'not_started'}
                        disabled={setStatus.isPending}
                        onChange={(event) =>
                          setStatus.mutate({ id: useCase.id, status: event.target.value })
                        }
                        className="text-xs font-semibold rounded-full px-2 py-1 border bg-navy-800 cursor-pointer focus:outline-none focus:ring-1 focus:ring-info"
                        style={{
                          color: STATUS_COLORS[useCase.status ?? 'not_started'],
                          borderColor: `${STATUS_COLORS[useCase.status ?? 'not_started']}66`,
                        }}
                      >
                        {STATUSES.map((status) => (
                          <option
                            key={status}
                            value={status}
                            style={{ color: '#E6EDF3', background: '#0B2026' }}
                          >
                            {STATUS_LABELS[status]}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <ReadinessBadge
                      readiness={useCase.readiness}
                      pendingPrereqs={useCase.pending_prereqs}
                    />
                  </td>
                  <td className="px-3 py-2.5 text-navy-300">{useCase.priority_score}</td>
                  <td className="px-3 py-2.5 text-lava-300 font-medium">
                    {fmtMoney(useCase.computed_value)}
                  </td>
                  <td className="px-3 py-2.5 text-success font-medium">
                    {useCase.realized && useCase.realized.value
                      ? fmtMoney(useCase.realized.value)
                      : '—'}
                  </td>
                  {!readOnly && (
                    <td className="px-3 py-2.5">
                      <button
                        aria-label={`Delete ${useCase.title}`}
                        className="text-navy-600 hover:text-lava"
                        onClick={(event) => {
                          event.stopPropagation()
                          if (confirm(`Delete "${useCase.title}"?`)) remove.mutate(useCase.id)
                        }}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div
          className={`grid gap-3 ${
            grouping === 'status' ? 'grid-cols-2 md:grid-cols-5' : 'grid-cols-1 md:grid-cols-3'
          }`}
        >
          {columns.map((column) => {
            const cards = rows.filter(column.match)
            return (
              <div
                key={column.key}
                className="bg-navy-800/60 border border-navy-600 rounded-card p-2"
              >
                <div className="flex items-center justify-between px-1 pb-2 mb-2 border-b border-navy-600">
                  <div
                    className="text-xs font-bold flex items-center gap-1.5"
                    style={{ color: column.color }}
                  >
                    <span className="w-2 h-2 rounded-full" style={{ background: column.color }} />
                    {column.label}
                  </div>
                  <span className="text-xs text-navy-500">{cards.length}</span>
                </div>
                <div className="space-y-2 max-h-[62vh] overflow-y-auto">
                  {cards.map((useCase) => (
                    <button
                      key={useCase.id}
                      onClick={() => onOpen?.(useCase.id)}
                      className="w-full text-left card p-2.5 hover:border-navy-500 transition-colors border-l-2"
                      style={{
                        borderLeftColor: useCase.readiness
                          ? READINESS_COLORS[useCase.readiness]
                          : '#2A4A56',
                      }}
                    >
                      <div className="text-sm font-medium leading-snug line-clamp-2 text-white">
                        {useCase.title}
                      </div>
                      <div className="flex items-center justify-between mt-1.5 text-xs">
                        <span
                          style={{ color: LOB_COLORS[lobName(useCase.lob_id)] ?? '#90A5B1' }}
                        >
                          {lobName(useCase.lob_id)}
                        </span>
                        <span className="text-lava-300">{fmtMoney(useCase.computed_value)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function SortableHeader({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <th
      className="text-left px-3 py-2.5 font-medium cursor-pointer hover:text-navy-300 select-none"
      onClick={onClick}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        <ArrowDownUp className="w-3 h-3 opacity-50" />
      </span>
    </th>
  )
}
