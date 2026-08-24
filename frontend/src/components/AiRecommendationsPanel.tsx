// The AI-recommendations shelf for the Portfolio landing.
//
// WHY THIS EXISTS
// ---------------
// The Portfolio used to render both AI panels — "Next-best use cases" and "Land
// these data sources next" — expanded inline at the top of the view. Each is a
// card that can grow to a dozen ranked rows, so the first thing a customer saw on
// landing was two blocks of AI output above their actual portfolio. The user's
// words: "the AI recommendations just can be a lot for the initial landing view."
//
// So this collapses them behind ONE clearly-labeled row that is closed on first
// load. The feature is not removed — one click opens it — it just no longer
// dominates the landing. The open/closed choice is persisted per browser so a
// customer who wants the shelf open keeps it open across visits.
//
// The two inner panels keep their own collapse + lazy-query behaviour: their
// LLM-backed queries stay `enabled: open` on the panel itself, so wrapping them
// here costs nothing until someone expands both this shelf and a panel inside it.

import { useState } from 'react'
import type { ReactNode } from 'react'
import { Bot, ChevronDown, ChevronRight, Sparkles } from 'lucide-react'

import { AI_RECS_OPEN_KEY, readBoolPref, writeBoolPref } from '../lib/prefs'

export function AiRecommendationsPanel({
  count,
  children,
}: {
  /** How many recommendation panels are inside — shown as "(N)" so the label is honest. */
  count: number
  children: ReactNode
}) {
  // Collapsed on first load; the persisted choice only ever OPENS it, never forces
  // it closed against a returning user who left it open.
  const [open, setOpen] = useState(() => readBoolPref(AI_RECS_OPEN_KEY, false))

  const toggle = () => {
    setOpen((current) => {
      const next = !current
      writeBoolPref(AI_RECS_OPEN_KEY, next)
      return next
    })
  }

  return (
    <div className="card p-0 overflow-hidden" data-gaAiRecsShelf="1">
      <button
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-lava/5"
        aria-expanded={open}
        onClick={toggle}
      >
        <div className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="w-4 h-4 text-navy-500" />
          ) : (
            <ChevronRight className="w-4 h-4 text-navy-500" />
          )}
          <Bot className="w-4 h-4 text-lava" />
          <span className="font-semibold text-white">AI recommendations</span>
          <span className="text-xs text-navy-500">({count})</span>
        </div>
        <Sparkles className="w-4 h-4 text-lava-300" />
      </button>

      {open && <div className="border-t border-navy-600 p-3 space-y-3">{children}</div>}
    </div>
  )
}
