// The dependency-respecting roadmap: three horizons, drag to re-sequence, and
// the board-ready one-pager that gets printed and handed round the room.
//
// The sequencing the agent returns is advisory — a customer will always want to
// move something. Moves are held in local state (never written back), so the
// generated sequence stays the source of truth until `Confirm & save` persists
// it. Prerequisite violations are therefore *flagged*, not prevented: the point
// is to show the cost of the decision, not to veto it.

import { useMemo, useState } from 'react'
import type { DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Printer, Sparkles, TriangleAlert, WandSparkles, X, Zap } from 'lucide-react'

import { api } from '../api'
import { useReadOnly } from '../context/RoleContext'
import { LOB_COLORS, fmtMoney } from '../constants'
import type { DashboardData, Lob, RoadmapItem, UseCase } from '../types'

type Horizon = RoadmapItem['horizon']

const HORIZONS: { key: Horizon; label: string; hint: string }[] = [
  { key: 'now', label: 'Now', hint: 'Wave 1 · shovel-ready foundations' },
  { key: 'next', label: 'Next', hint: 'Wave 2 · fast-follows' },
  { key: 'later', label: 'Later', hint: 'Wave 3+ · dependent / higher-effort' },
]

/** Later horizons sort after earlier ones — the ordering a violation is measured against. */
const HORIZON_RANK: Record<Horizon, number> = { now: 1, next: 2, later: 3 }

export default function RoadmapView({
  lobs = [],
  onOpen,
}: {
  lobs?: Lob[]
  onOpen?: (useCaseId: number) => void
}) {
  const queryClient = useQueryClient()
  const readOnly = useReadOnly()
  const [sheetOpen, setSheetOpen] = useState(false)
  const [moved, setMoved] = useState<Record<number, Horizon>>({})

  const roadmap = useQuery({
    queryKey: ['roadmap-gen'],
    queryFn: () => api.generateRoadmap(false),
  })
  const useCases = useQuery({ queryKey: ['use-cases'], queryFn: () => api.useCases() })
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  const enables = useQuery({ queryKey: ['enables'], queryFn: api.enables })

  const save = useMutation({
    mutationFn: () => api.generateRoadmap(true),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roadmap-items'] }),
  })

  /** to_use_case_id → the use cases that must be built first. */
  const prereqsOf = useMemo(() => {
    const map = new Map<number, number[]>()
    for (const edge of enables.data ?? []) {
      map.set(edge.to_use_case_id, [
        ...(map.get(edge.to_use_case_id) ?? []),
        edge.from_use_case_id,
      ])
    }
    return map
  }, [enables.data])

  // Flattened so a moved card is a single item whose horizon is overridden,
  // rather than a mutation of the three server-owned lists.
  const items = useMemo<RoadmapItem[]>(() => {
    const horizons = roadmap.data?.horizons
    if (!horizons) return []
    return HORIZONS.flatMap((column) =>
      horizons[column.key].map((item) => ({
        ...item,
        horizon: moved[item.use_case_id] ?? item.horizon,
      })),
    )
  }, [roadmap.data, moved])

  const byId = new Map(items.map((item) => [item.use_case_id, item]))

  const violations = (item: RoadmapItem): string[] => {
    const titles: string[] = []
    for (const prereqId of prereqsOf.get(item.use_case_id) ?? []) {
      const prereq = byId.get(prereqId)
      if (prereq && HORIZON_RANK[prereq.horizon] > HORIZON_RANK[item.horizon]) {
        titles.push(prereq.title)
      }
    }
    return titles
  }

  const move = (useCaseId: number, horizon: Horizon) =>
    setMoved((current) => ({ ...current, [useCaseId]: horizon }))

  if (roadmap.isError) {
    return (
      <div className="text-lava-300 text-sm">Couldn't generate the roadmap. Please retry.</div>
    )
  }
  if (roadmap.isLoading) {
    return <div className="text-navy-400">Generating dependency-respecting roadmap…</div>
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 text-sm text-navy-400">
          <Sparkles className="w-4 h-4 text-lava" />
          {`Auto-sequenced into dependency-respecting waves (${roadmap.data?.waves ?? 0} waves). ` +
            'Drag between horizons; violations flagged.'}
        </div>
        {!readOnly && (
          <div className="flex gap-2">
            <button
              className="btn-secondary text-sm"
              onClick={() => {
                setMoved({})
                roadmap.refetch()
              }}
            >
              <WandSparkles className="w-4 h-4" /> Regenerate
            </button>
            <button className="btn-secondary text-sm" onClick={() => setSheetOpen(true)}>
              <FileText className="w-4 h-4" /> Exec one-pager
            </button>
            <button
              className="btn-primary text-sm"
              disabled={save.isPending}
              onClick={() => save.mutate()}
            >
              Confirm &amp; save
            </button>
          </div>
        )}
      </div>

      {save.isSuccess ? (
        <div className="text-xs text-success">{`Saved ${save.data?.persisted} roadmap items.`}</div>
      ) : null}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {HORIZONS.map((column) => {
          const cards = items
            .filter((item) => item.horizon === column.key)
            .sort(
              (a, b) =>
                (a.wave ?? 0) - (b.wave ?? 0) || (b.opportunity ?? 0) - (a.opportunity ?? 0),
            )
          const total = cards.reduce((sum, card) => sum + (card.value_mm || 0), 0)
          return (
            <div
              key={column.key}
              className="bg-navy-800/60 border border-navy-600 rounded-card p-3"
              onDragOver={(event: DragEvent<HTMLDivElement>) => event.preventDefault()}
              onDrop={
                !readOnly
                  ? (event: DragEvent<HTMLDivElement>) => {
                      const useCaseId = Number(event.dataTransfer.getData('text/plain'))
                      if (useCaseId) move(useCaseId, column.key)
                    }
                  : undefined
              }
            >
              <div className="flex items-center justify-between border-b border-navy-600 pb-2 mb-2">
                <div>
                  <div className="font-bold text-white">{column.label}</div>
                  <div className="text-[11px] text-navy-500">{column.hint}</div>
                </div>
                <div className="text-right text-xs">
                  <div className="text-lava-300 font-semibold">{fmtMoney(total)}</div>
                  <div className="text-navy-500">{`${cards.length} UCs`}</div>
                </div>
              </div>

              <div className="space-y-2 max-h-[64vh] overflow-y-auto">
                {cards.map((card) => {
                  const blockedBy = violations(card)
                  const color = (card.lob_name ? LOB_COLORS[card.lob_name] : undefined) ?? '#2A4A56'
                  return (
                    <div
                      key={card.use_case_id}
                      draggable={!readOnly}
                      onDragStart={(event: DragEvent<HTMLDivElement>) =>
                        event.dataTransfer.setData('text/plain', String(card.use_case_id))
                      }
                      onClick={() => onOpen?.(card.use_case_id)}
                      className="card p-2.5 cursor-pointer hover:border-navy-500 border-l-2"
                      style={{ borderLeftColor: color }}
                    >
                      <div className="text-sm font-medium text-white leading-snug line-clamp-2">
                        {card.title}
                      </div>
                      <div className="flex items-center justify-between mt-1 text-xs">
                        <span
                          style={{
                            color:
                              (card.lob_name ? LOB_COLORS[card.lob_name] : undefined) ?? '#90A5B1',
                          }}
                        >
                          {card.lob_name}
                        </span>
                        <span className="text-lava-300">{fmtMoney(card.value_mm)}</span>
                      </div>
                      {blockedBy.length > 0 ? (
                        <div className="mt-1 text-[11px] text-warning flex items-center gap-1">
                          <TriangleAlert className="w-3 h-3" /> after prereq: {blockedBy[0]}
                          {blockedBy.length > 1 ? ` +${blockedBy.length - 1}` : ''}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
                {cards.length === 0 ? (
                  <div className="text-xs text-navy-500 py-4 text-center">Drop use cases here</div>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>

      {sheetOpen ? (
        <ExecOnePager
          items={items}
          ucs={useCases.data ?? []}
          lobs={lobs}
          totals={dashboard.data?.totals}
          onClose={() => setSheetOpen(false)}
        />
      ) : null}
    </div>
  )
}

/**
 * The print artefact. It is a light sheet inside a dark app on purpose: this is
 * the thing that leaves the room as a PDF, and `.exec-sheet` is what the print
 * stylesheet promotes to the whole page.
 */
function ExecOnePager({
  items,
  ucs,
  lobs,
  totals,
  onClose,
}: {
  items: RoadmapItem[]
  ucs: UseCase[]
  lobs: Lob[]
  totals?: DashboardData['totals']
  onClose: () => void
}) {
  // The dashboard already rolls these up; the portfolio is the fallback when the
  // analytics query has not landed yet, so the sheet is never blank.
  const potential =
    totals?.hypothesized_mm ?? ucs.reduce((sum, uc) => sum + (uc.computed_value ?? 0), 0)
  const buildable =
    totals?.hypothesized_buildable_mm ??
    ucs.reduce((sum, uc) => sum + (uc.readiness !== 'blocked' ? (uc.computed_value ?? 0) : 0), 0)
  const realized =
    totals?.realized_mm ?? ucs.reduce((sum, uc) => sum + (uc.realized?.value ?? 0), 0)
  const liveCount = ucs.filter(
    (uc) => uc.status === 'live' || uc.status === 'value_realized',
  ).length
  const shovelReady = ucs.filter((uc) => uc.readiness === 'shovel_ready').length

  const topUseCases = [...ucs]
    .sort((a, b) => (b.computed_value ?? 0) - (a.computed_value ?? 0))
    .slice(0, 6)

  const horizonItems = (horizon: Horizon) =>
    items
      .filter((item) => item.horizon === horizon)
      .sort((a, b) => (b.value_mm ?? 0) - (a.value_mm ?? 0))

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '') : ''

  const stats: [string, string, string][] = [
    ['Buildable / yr', fmtMoney(buildable), `of ${fmtMoney(potential)} potential`],
    ['Realized / yr', fmtMoney(realized), ''],
    ['Live use cases', String(liveCount), ''],
    ['Shovel-ready', String(shovelReady), ''],
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 print:hidden" onClick={onClose} />
      <div
        className="relative bg-white text-slate-900 w-full max-w-3xl max-h-[92vh] overflow-y-auto rounded-lg exec-sheet"
        style={{ padding: 32 }}
      >
        <div className="flex items-center justify-between mb-4 print:hidden">
          <span className="text-xs text-slate-500">Board-ready one-pager</span>
          <div className="flex gap-2">
            <button
              className="text-sm px-3 py-1.5 rounded bg-slate-900 text-white flex items-center gap-1.5"
              onClick={() => window.print()}
            >
              <Printer className="w-4 h-4" /> Print / PDF
            </button>
            <button
              aria-label="Close"
              className="text-slate-500 hover:text-slate-900"
              onClick={onClose}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3 border-b-2 pb-3 mb-4" style={{ borderColor: '#FF3621' }}>
          <div
            className="w-9 h-9 rounded flex items-center justify-center"
            style={{ background: '#FF3621' }}
          >
            <Zap className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Data &amp; AI Roadmap</h1>
            <p className="text-sm text-slate-500">
              Power &amp; Utilities — Use Case Portfolio, Value &amp; Roadmap
            </p>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-3 mb-5">
          {stats.map(([label, value, sub]) => (
            <div key={label} className="border rounded p-3" style={{ borderColor: '#e2e8f0' }}>
              <div className="text-2xl font-bold" style={{ color: '#FF3621' }}>
                {value}
              </div>
              <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
              {sub ? <div className="text-[10px] text-slate-400 mt-0.5">{sub}</div> : null}
            </div>
          ))}
        </div>

        <h2 className="font-semibold text-lg mb-2">Highest-value use cases</h2>
        <table className="w-full text-sm mb-5">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-1">Use case</th>
              <th>Domain</th>
              <th>Status</th>
              <th className="text-right">Hyp. value/yr</th>
            </tr>
          </thead>
          <tbody>
            {topUseCases.map((uc) => (
              <tr key={uc.id} className="border-b border-slate-100">
                <td className="py-1.5 font-medium">{uc.title}</td>
                <td className="text-slate-600">{lobName(uc.lob_id)}</td>
                <td className="text-slate-600">{(uc.status ?? '').replace('_', ' ')}</td>
                <td className="text-right font-semibold" style={{ color: '#FF3621' }}>
                  {fmtMoney(uc.computed_value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2 className="font-semibold text-lg mb-2">Roadmap (dependency-respecting waves)</h2>
        <div className="grid grid-cols-3 gap-3">
          {HORIZONS.map((column) => (
            <div key={column.key} className="border rounded p-2" style={{ borderColor: '#e2e8f0' }}>
              <div className="font-bold uppercase text-xs mb-1" style={{ color: '#FF3621' }}>
                {column.key}
              </div>
              <ul className="text-xs space-y-1">
                {horizonItems(column.key)
                  .slice(0, 8)
                  .map((item) => (
                    <li key={item.use_case_id} className="flex justify-between gap-2">
                      <span className="break-words">{item.title}</span>
                      <span className="text-slate-500 whitespace-nowrap">
                        {fmtMoney(item.value_mm)}
                      </span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="text-[10px] text-slate-400 mt-5 pt-2 border-t">
          Generated by AI Value Flywheel · Powered by Databricks
        </div>
      </div>
    </div>
  )
}
