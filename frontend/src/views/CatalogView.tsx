// The reference library: every predefined use case, with the ones you have not
// thought of surfaced at the top.
//
// This view is the answer to "we don't know what to build". It is lazy-loaded
// because the catalog is the second thing a customer opens, not the first, and
// the default export is what names the chunk.

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Lightbulb, Plus, Sparkles } from 'lucide-react'

import { api } from '../api'
import { LOB_COLORS, fmtMoney, subVerticalLabel } from '../constants'
import { ReadinessBadge } from '../components/Badges'
import { matchesUseCase, useFilters } from '../context/FilterContext'
import { useReadOnly } from '../context/RoleContext'
import type { Lob } from '../types'

export default function CatalogView({
  lobs = [],
  onOpen,
}: {
  lobs?: Lob[]
  onOpen?: (useCaseId: number) => void
}) {
  const queryClient = useQueryClient()
  const { filters } = useFilters()
  const readOnly = useReadOnly()
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const catalog = useQuery({
    queryKey: ['use-cases', 'catalog'],
    queryFn: () => api.useCases('catalog'),
  })
  const recommended = useQuery({
    queryKey: ['recommend-catalog'],
    queryFn: () => api.recommendCatalog(8),
  })

  // Adding to the portfolio moves value between every roll-up on screen, so the
  // KPI strip and the recommender both have to be refetched, not just the table.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['use-cases'] })
    queryClient.invalidateQueries({ queryKey: ['recommend-catalog'] })
    queryClient.invalidateQueries({ queryKey: ['analytics-dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
  }

  const toggleOne = useMutation({
    mutationFn: ({ id, inp }: { id: number; inp: boolean }) => api.setInPortfolio(id, inp),
    onSuccess: refresh,
  })
  const addMany = useMutation({
    mutationFn: (ids: number[]) => api.bulkPortfolio(ids, true),
    onSuccess: () => {
      setSelected(new Set())
      refresh()
    },
  })

  const useCases = catalog.data ?? []
  const visible = useMemo(
    () => useCases.filter((useCase) => matchesUseCase(useCase, filters)),
    [useCases, filters],
  )
  const inPortfolioCount = useCases.filter((useCase) => useCase.in_portfolio).length

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '—') : '—'

  // A checkbox can survive the row it belongs to flipping to in-portfolio, so the
  // bulk set is re-filtered at submit time rather than trusted as-is.
  const pending = [...selected].filter(
    (id) => !useCases.find((useCase) => useCase.id === id)?.in_portfolio,
  )

  return (
    <div className="space-y-4">
      <div className="card p-0 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-navy-600">
          <Lightbulb className="w-4 h-4 text-lava" />
          <span className="font-semibold text-white">Recommended to add</span>
          <span className="text-xs text-navy-500">
            ideas to consider you may not be thinking about — ranked by value × readiness ÷ effort
          </span>
        </div>
        <div className="p-3">
          {recommended.isLoading && (
            <div className="text-sm text-navy-400 flex items-center gap-2">
              <Sparkles className="w-4 h-4 animate-pulse text-lava" /> Ranking catalog ideas…
            </div>
          )}
          {recommended.isError && (
            <div className="text-sm text-lava-300">Couldn't load recommendations. Please retry.</div>
          )}
          {recommended.data && recommended.data.recommendations.length === 0 && (
            <div className="text-sm text-navy-400">
              Every catalog idea is already in your portfolio. Nice — add your own next.
            </div>
          )}
          {recommended.data && recommended.data.recommendations.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {recommended.data.recommendations.map((rec, index) => (
                <div key={rec.id} className="card p-2.5 flex items-start justify-between gap-2">
                  <button className="text-left min-w-0 flex-1" onClick={() => onOpen?.(rec.id)}>
                    <div className="font-medium text-white flex items-center gap-1.5">
                      <span className="text-lava-300">#{index + 1}</span>
                      <span className="truncate">{rec.title}</span>
                      {rec.track === 'do_now' && (
                        <span
                          className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0"
                          style={{
                            background: 'rgba(0,169,114,0.18)',
                            color: '#9ED6C4',
                            border: '1px solid rgba(0,169,114,0.4)',
                          }}
                        >
                          Do now
                        </span>
                      )}
                      {rec.track === 'fast_follow' && (
                        <span
                          className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full shrink-0"
                          style={{
                            background: 'rgba(124,107,255,0.18)',
                            color: '#B3A7FF',
                            border: '1px solid rgba(124,107,255,0.45)',
                          }}
                        >
                          Fast-follow
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-navy-400 mt-0.5">
                      {rec.lob_name} · {rec.readiness ?? '—'} · {fmtMoney(rec.value_mm)}
                    </div>
                    <div className="text-xs text-navy-300 mt-1">{rec.rationale}</div>
                  </button>
                  {!readOnly && (
                    <button
                      className="btn-primary text-xs whitespace-nowrap flex items-center gap-1 disabled:opacity-50"
                      disabled={toggleOne.isPending}
                      onClick={() => toggleOne.mutate({ id: rec.id, inp: true })}
                    >
                      <Plus className="w-3.5 h-3.5" /> Add
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-navy-400">
          <span className="text-white font-medium">{visible.length}</span> of {useCases.length}{' '}
          catalog use cases · <span className="text-success font-medium">{inPortfolioCount}</span> in
          your portfolio
        </div>
        {!readOnly && pending.length > 0 && (
          <button
            className="btn-primary text-sm flex items-center gap-1.5 disabled:opacity-50"
            disabled={addMany.isPending}
            onClick={() => addMany.mutate(pending)}
          >
            <Plus className="w-4 h-4" /> Add {pending.length} selected to portfolio
          </button>
        )}
      </div>

      {catalog.isLoading && <div className="text-navy-400">Loading catalog…</div>}
      {catalog.isError && (
        <div className="text-lava-300 text-sm">Couldn't load the catalog. Please retry.</div>
      )}

      {!catalog.isLoading && !catalog.isError && (
        <div className="card p-0 overflow-x-auto">
          <table data-gaCustomerVisibility="1" className="w-full text-sm min-w-[900px]">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr>
                {!readOnly && <th className="w-10 px-3 py-2.5" />}
                <th className="text-left px-3 py-2.5 font-medium">Use case</th>
                <th className="text-left px-3 py-2.5 font-medium">Domain</th>
                <th className="text-left px-3 py-2.5 font-medium">Focus</th>
                <th className="text-left px-3 py-2.5 font-medium">Readiness</th>
                <th className="text-right px-3 py-2.5 font-medium">Value/yr</th>
                <th className="text-right px-3 py-2.5 font-medium">In portfolio</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((useCase) => (
                <tr
                  key={useCase.id}
                  className="border-b border-navy-600 hover:bg-lava/5 cursor-pointer"
                  onClick={() => onOpen?.(useCase.id)}
                >
                  {!readOnly && (
                    <td className="px-3 py-2.5" onClick={(event) => event.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select ${useCase.title}`}
                        checked={selected.has(useCase.id)}
                        disabled={useCase.in_portfolio ?? false}
                        onChange={(event) => {
                          const next = new Set(selected)
                          if (event.target.checked) next.add(useCase.id)
                          else next.delete(useCase.id)
                          setSelected(next)
                        }}
                      />
                    </td>
                  )}
                  <td className="px-3 py-2.5 font-medium text-white">{useCase.title}</td>
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-1.5 text-navy-300">
                      <span
                        className="w-2 h-2 rounded-full"
                        style={{ background: LOB_COLORS[lobName(useCase.lob_id)] ?? '#618794' }}
                      />
                      {lobName(useCase.lob_id)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-navy-400">
                    {subVerticalLabel(useCase.sub_vertical)}
                  </td>
                  <td className="px-3 py-2.5">
                    <ReadinessBadge
                      readiness={useCase.readiness}
                      pendingPrereqs={useCase.pending_prereqs}
                    />
                  </td>
                  <td className="px-3 py-2.5 text-right text-lava-300 font-medium">
                    {fmtMoney(useCase.computed_value)}
                  </td>
                  <td className="px-3 py-2.5 text-right" onClick={(event) => event.stopPropagation()}>
                    {readOnly ? (
                      useCase.in_portfolio ? (
                        <span className="badge-muted inline-flex items-center gap-1 text-success">
                          <CircleCheck className="w-3.5 h-3.5" /> In portfolio
                        </span>
                      ) : (
                        <span className="text-xs text-navy-500">—</span>
                      )
                    ) : useCase.in_portfolio ? (
                      <button
                        className="badge-muted inline-flex items-center gap-1 text-success hover:text-lava-300 disabled:opacity-50"
                        disabled={toggleOne.isPending}
                        title="In your portfolio — click to remove"
                        onClick={() => toggleOne.mutate({ id: useCase.id, inp: false })}
                      >
                        <CircleCheck className="w-3.5 h-3.5" /> In portfolio
                      </button>
                    ) : (
                      <button
                        className="badge-muted inline-flex items-center gap-1 hover:text-white hover:border-info disabled:opacity-50"
                        disabled={toggleOne.isPending}
                        title="Add to your portfolio"
                        onClick={() => toggleOne.mutate({ id: useCase.id, inp: true })}
                      >
                        <Plus className="w-3.5 h-3.5" /> Add
                      </button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
