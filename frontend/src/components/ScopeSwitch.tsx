// The two ways to read the same use-case list: the ones you have committed to,
// and the whole library you could commit to.
//
// These used to be two top-level tabs ("Portfolio" and "Use Case Catalog") even
// though both render `/api/use-cases` and differ only by `?scope=`. That made a
// data filter look like a destination, and it split one question — "what should
// we build" — across two places you had to know to visit. It is a switch, so it
// looks like one, and it reuses the same segmented treatment as the
// table/Kanban switch inside the portfolio.

import type { ReactNode } from 'react'
import { BookOpen, Layers } from 'lucide-react'

/** Only the two scopes a customer picks between; `all` is not a UI choice. */
export type UseCaseView = 'portfolio' | 'catalog'

const SCOPES: { id: UseCaseView; label: string; icon: ReactNode; hint: string }[] = [
  {
    id: 'portfolio',
    label: 'Your portfolio',
    icon: <Layers className="w-4 h-4" />,
    hint: 'The use cases this organisation has committed to.',
  },
  {
    id: 'catalog',
    label: 'Catalog',
    icon: <BookOpen className="w-4 h-4" />,
    hint: 'Every predefined use case, with the ones to consider ranked first.',
  },
]

export function ScopeSwitch({
  scope,
  setScope,
}: {
  scope: UseCaseView
  setScope: (scope: UseCaseView) => void
}) {
  const active = SCOPES.find((candidate) => candidate.id === scope)

  return (
    <div className="flex items-center gap-3 flex-wrap" data-gaScopeSwitch="1">
      <div
        className="bg-navy-700 border border-navy-600 rounded p-0.5 flex"
        role="tablist"
        aria-label="Use case scope"
      >
        {SCOPES.map((candidate) => (
          <button
            key={candidate.id}
            role="tab"
            aria-selected={scope === candidate.id}
            onClick={() => setScope(candidate.id)}
            className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
              scope === candidate.id ? 'bg-lava/20 text-lava-300' : 'text-navy-400'
            }`}
          >
            {candidate.icon}
            {candidate.label}
          </button>
        ))}
      </div>
      <span className="text-xs text-navy-500">{active?.hint}</span>
    </div>
  )
}
