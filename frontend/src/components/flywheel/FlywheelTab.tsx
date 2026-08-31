// The Value Flywheel tab: two views of the same dependency data, one scope switch.
//
// Lazy-loaded from App because xyflow and dagre are the two heaviest dependencies
// in the bundle and neither is needed to render the portfolio. Scope lives here,
// not inside either child, so flipping between the map and the editor keeps you
// looking at the same universe.

import { useState } from 'react'
import { BookOpen, GitFork, Layers, Radar } from 'lucide-react'
import { BlastRadius } from './BlastRadius'
import { DependencyGraph } from './DependencyGraph'

type SubTab = 'blast' | 'graph'
type Scope = 'portfolio' | 'catalog'

export default function FlywheelTab({
  focusUcId,
  onOpen,
}: {
  focusUcId: number | null
  onOpen: (id: number) => void
}) {
  const [subTab, setSubTab] = useState<SubTab>('blast')
  const [scope, setScope] = useState<Scope>('portfolio')

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex">
          <button
            className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
              subTab === 'blast' ? 'bg-lava/20 text-lava-300' : 'text-navy-400'
            }`}
            onClick={() => setSubTab('blast')}
          >
            <Radar className="w-4 h-4" /> Blast Radius (by LOB)
          </button>
          <button
            className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
              subTab === 'graph' ? 'bg-lava/20 text-lava-300' : 'text-navy-400'
            }`}
            onClick={() => setSubTab('graph')}
          >
            <GitFork className="w-4 h-4" /> Dependency Graph
          </button>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-navy-500">Scope</span>
          <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex">
            <button
              className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
                scope === 'portfolio' ? 'bg-info/20 text-info' : 'text-navy-400'
              }`}
              onClick={() => setScope('portfolio')}
              title="Your confirmed portfolio"
            >
              <Layers className="w-4 h-4" /> Portfolio
            </button>
            <button
              className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
                scope === 'catalog' ? 'bg-info/20 text-info' : 'text-navy-400'
              }`}
              onClick={() => setScope('catalog')}
              title="Full catalog universe (all 240) — see the opportunity map"
            >
              <BookOpen className="w-4 h-4" /> Catalog
            </button>
          </div>
        </div>
      </div>

      {subTab === 'blast' ? (
        <BlastRadius onOpen={onOpen} focusUcId={focusUcId} scope={scope} />
      ) : (
        <DependencyGraph scope={scope} />
      )}
    </div>
  )
}
