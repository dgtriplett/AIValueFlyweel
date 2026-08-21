// "AI: Land these data sources next" — shared by the Portfolio and Data Assets.
//
// It answers the question a data leader actually asks: of everything not yet
// landed, which one source unblocks the most value? It appears on both views
// because that question arrives from two directions — from the portfolio ("why
// is this blocked?") and from the sources ("what should I land?"). Open by
// default on Data Assets, closed on the Portfolio, hence the prop.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronRight, Database, Sparkles, TrendingUp, Zap } from 'lucide-react'

import { api } from '../api'
import { fmtDollars, fmtMoney } from '../constants'

export function SourceRecommendPanel({
  defaultOpen = false,
  onOpenUseCase,
}: {
  defaultOpen?: boolean
  onOpenUseCase?: (useCaseId: number) => void
}) {
  const [open, setOpen] = useState(defaultOpen)
  const [expanded, setExpanded] = useState<number | null>(null)
  const sources = useQuery({
    queryKey: ['source-recommendations'],
    queryFn: api.sourceRecommendations,
    enabled: open,
  })

  return (
    <div className="card p-0 overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-lava/5"
        onClick={() => setOpen((current) => !current)}
      >
        <div className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="w-4 h-4 text-navy-500" />
          ) : (
            <ChevronRight className="w-4 h-4 text-navy-500" />
          )}
          <Database className="w-4 h-4 text-info" />
          <span className="font-semibold text-white">AI: Land these data sources next</span>
          <span className="text-xs text-navy-500 hidden sm:inline">
            ranked by use cases unblocked + value unlocked
          </span>
        </div>
        <Sparkles className="w-4 h-4 text-lava-300" />
      </button>

      {open && (
        <div className="border-t border-navy-600 p-3">
          {sources.isLoading && (
            <div className="text-sm text-navy-400 flex items-center gap-2">
              <Sparkles className="w-4 h-4 animate-pulse text-lava" /> Ranking not-yet-landed sources
              by immediate value…
            </div>
          )}
          {sources.isError && (
            <div className="text-sm text-lava-300">Couldn't load recommendations. Please retry.</div>
          )}
          {sources.data && sources.data.recommendations.length === 0 && (
            <div className="text-sm text-navy-400">
              Every data source that would unblock a use case is already landed. Nothing to
              prioritize.
            </div>
          )}
          {sources.data && sources.data.recommendations.length > 0 && (
            <div className="space-y-2">
              <div className="text-[11px] text-navy-500 flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5 text-success" />
                Landing all recommended sources would make{' '}
                <span className="text-success font-medium">
                  {sources.data.summary.total_flips_available}
                </span>{' '}
                use cases shovel-ready and unlock{' '}
                <span className="text-success font-medium">
                  {fmtMoney(sources.data.summary.total_value_unlockable_mm)}
                </span>
                /yr.
              </div>
              {sources.data.recommendations.map((rec, index) => {
                // Category is the useful label, but auto-captured rows only carry a
                // source_system — and either can be missing, hence the separator strip.
                const label = `${rec.asset.source_category || rec.asset.source_system || ''} · ${rec.asset.module}`.replace(
                  /^ · /,
                  '',
                )
                const isOpen = expanded === rec.asset.id
                return (
                  <div key={rec.asset.id} className="card p-2.5">
                    <button
                      className="w-full text-left"
                      onClick={() => setExpanded(isOpen ? null : rec.asset.id)}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-medium text-white flex items-center gap-1.5 min-w-0">
                          <span className="text-info shrink-0">#{index + 1}</span>
                          <span className="truncate">{label}</span>
                          {rec.asset.vendor && (
                            <span className="text-navy-500 text-xs shrink-0">
                              ({rec.asset.vendor})
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 shrink-0 text-sm">
                          {rec.flips_to_ready > 0 && (
                            <span
                              className="text-success flex items-center gap-1"
                              title="Use cases that become shovel-ready"
                            >
                              <Zap className="w-3.5 h-3.5" />
                              {rec.flips_to_ready}
                            </span>
                          )}
                          <span className="text-lava-300">{fmtMoney(rec.value_unlocked_mm)}</span>
                          {isOpen ? (
                            <ChevronDown className="w-4 h-4 text-navy-500" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-navy-500" />
                          )}
                        </div>
                      </div>
                      <div className="text-xs text-navy-300 mt-1 flex items-center gap-1">
                        <Bot className="w-3 h-3 text-info shrink-0" /> {rec.rationale}
                      </div>
                    </button>

                    {isOpen && (
                      <div className="mt-2 pt-2 border-t border-navy-600 space-y-2">
                        <div className="text-[11px] text-navy-500">
                          Ingestion effort {rec.asset.ingest_effort || 'M'} · est.{' '}
                          {fmtDollars(rec.ingest_cost_low ?? 0)}–
                          {fmtDollars(rec.ingest_cost_high ?? 0)} · currently{' '}
                          {(rec.asset.ingestion_status ?? '').replace('_', ' ')}
                        </div>
                        {rec.unlocks.length > 0 && (
                          <div>
                            <div className="text-[11px] font-semibold text-success uppercase tracking-wide mb-1">
                              Becomes shovel-ready ({rec.unlocks.length})
                            </div>
                            <div className="space-y-0.5">
                              {rec.unlocks.map((item) => (
                                <button
                                  key={item.id}
                                  className="w-full text-left flex items-center justify-between text-xs text-navy-300 hover:text-white py-0.5"
                                  onClick={() => onOpenUseCase?.(item.id)}
                                >
                                  <span className="truncate">
                                    {item.title}{' '}
                                    <span className="text-navy-600">· {item.lob}</span>
                                  </span>
                                  <span className="text-lava-300 shrink-0 ml-2">
                                    {fmtMoney(item.value_mm)}
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        {rec.awaiting_prereqs && rec.awaiting_prereqs.length > 0 && (
                          <div>
                            <div
                              className="text-[11px] font-semibold uppercase tracking-wide mb-1"
                              style={{ color: '#B3A7FF' }}
                            >
                              Data-ready, awaiting prerequisites ({rec.awaiting_prereqs_count})
                            </div>
                            <div className="space-y-0.5">
                              {rec.awaiting_prereqs.map((item) => (
                                <button
                                  key={item.id}
                                  className="w-full text-left flex items-center justify-between text-xs text-navy-300 hover:text-white py-0.5"
                                  onClick={() => onOpenUseCase?.(item.id)}
                                >
                                  <span className="truncate">
                                    {item.title}{' '}
                                    <span className="text-navy-600">
                                      · {item.lob} · needs prereq builds
                                    </span>
                                  </span>
                                  <span className="shrink-0 ml-2" style={{ color: '#B3A7FF' }}>
                                    {fmtMoney(item.value_mm)}
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        {rec.sets_up.length > 0 && (
                          <div>
                            <div className="text-[11px] font-semibold text-navy-400 uppercase tracking-wide mb-1">
                              Also progresses ({rec.sets_up_count})
                            </div>
                            <div className="space-y-0.5">
                              {rec.sets_up.map((item) => (
                                <button
                                  key={item.id}
                                  className="w-full text-left flex items-center justify-between text-xs text-navy-400 hover:text-white py-0.5"
                                  onClick={() => onOpenUseCase?.(item.id)}
                                >
                                  <span className="truncate">
                                    {item.title}{' '}
                                    <span className="text-navy-600">
                                      · still needs {item.still_needs} more
                                    </span>
                                  </span>
                                  <span className="text-navy-500 shrink-0 ml-2">
                                    {fmtMoney(item.value_mm)}
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
