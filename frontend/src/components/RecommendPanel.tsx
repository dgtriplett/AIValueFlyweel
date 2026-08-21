// "AI: Next-best use cases" — the collapsible that opens the Portfolio.
//
// The query is `enabled: open` because the recommender calls an LLM and takes
// seconds: paying for that on every portfolio render, for a panel most visits
// never expand, is the wrong trade.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronRight, Sparkles } from 'lucide-react'

import { api } from '../api'
import { fmtMoney } from '../constants'

export function RecommendPanel({ onOpen }: { onOpen?: (useCaseId: number) => void }) {
  const [open, setOpen] = useState(false)
  const recommend = useQuery({
    queryKey: ['recommend'],
    queryFn: () => api.recommend(6),
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
          <Bot className="w-4 h-4 text-lava" />
          <span className="font-semibold text-white">AI: Next-best use cases</span>
          <span className="text-xs text-navy-500">
            value × readiness ÷ effort, with fast-follow narrative
          </span>
        </div>
        <Sparkles className="w-4 h-4 text-lava-300" />
      </button>

      {open && (
        <div className="border-t border-navy-600 p-3">
          {recommend.isLoading && (
            <div className="text-sm text-navy-400 flex items-center gap-2">
              <Sparkles className="w-4 h-4 animate-pulse text-lava" /> The AI is analyzing your live
              use cases + landed data to rank fast-follows… (a few seconds)
            </div>
          )}
          {/* Both error lines render together. It ships that way; changing the copy
              is a product decision, not a reconstruction one. */}
          {recommend.isError && (
            <div className="text-sm text-warning">Couldn't reach the recommender. Please retry.</div>
          )}
          {recommend.isError && (
            <div className="text-sm text-lava-300">Couldn't load recommendations. Please retry.</div>
          )}
          {recommend.data && (
            <div className="space-y-2">
              <div className="text-[11px] text-navy-500">
                Ranked by {recommend.data.used_llm ? recommend.data.model : 'opportunity heuristic'}.
              </div>
              {!recommend.data.used_llm && recommend.data.fallback_note && (
                <div className="text-[11px] text-warning">{recommend.data.fallback_note}</div>
              )}
              {recommend.data.recommendations.map((rec, index) => (
                <button
                  key={rec.id}
                  className="w-full text-left card p-2.5 hover:border-navy-500"
                  onClick={() => onOpen?.(rec.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium text-white flex items-center gap-1.5 min-w-0">
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
                    <span className="text-lava-300 text-sm shrink-0">{fmtMoney(rec.value_mm)}</span>
                  </div>
                  <div className="text-xs text-navy-400 mt-0.5">
                    {rec.lob_name} · {rec.readiness} · effort {rec.effort_tshirt} · opp{' '}
                    {rec.opportunity_score}
                  </div>
                  {rec.rationale && (
                    <div className="text-xs text-navy-300 mt-1">{rec.rationale}</div>
                  )}
                  {rec.narrative && (
                    <div className="text-xs text-success mt-0.5 flex items-center gap-1">
                      <Sparkles className="w-3 h-3" /> {rec.narrative}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
