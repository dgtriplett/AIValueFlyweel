// The compact label/value strip the ported console views open with.
//
// This is the console's `.stat` block (`index.html`) and its `metricCell()`
// (`console.js:3341-3351`), which nine views used. It is deliberately NOT
// `<StatCard>`: a StatCard is a bordered card with an icon, sized for the four or
// five numbers at the top of the app, and a row of eight of them for a view's own
// summary drowns the table underneath. This is the lighter weight tier.
//
// The delta is the part worth keeping. Trend shows "buildable value, +4.2 since
// the first snapshot", and `lowerIsBetter` exists because on the one metric where
// down is good — blocked use cases — a green "+3" would be a lie.

import type { ReactNode } from 'react'

export interface Stat {
  label: string
  value: ReactNode
  /** Signed change. `0`, `null` and `undefined` all render no badge. */
  change?: number | null
  /** Flips the delta colour: a rise in "blocked" is bad news, not good. */
  lowerIsBetter?: boolean
}

function Delta({ change, lowerIsBetter }: { change: number; lowerIsBetter?: boolean }) {
  const good = lowerIsBetter ? change < 0 : change > 0
  // `+` only: a negative number already carries its sign.
  const sign = change > 0 ? '+' : ''
  const rounded = Number.isInteger(change) ? change : Number(change.toFixed(2))
  return (
    <span
      className="text-[11px] font-medium ml-1.5"
      style={{ color: good ? '#00A972' : '#FFAB00' }}
    >
      {`${sign}${rounded}`}
    </span>
  )
}

export function StatStrip({ stats }: { stats: Stat[] }) {
  return (
    <div className="flex flex-wrap gap-x-8 gap-y-3">
      {stats.map((stat) => (
        <div key={stat.label}>
          <div className="text-[11px] uppercase tracking-wide text-navy-400">{stat.label}</div>
          <div className="text-xl font-semibold text-white font-mono flex items-baseline">
            {stat.value}
            {stat.change != null && stat.change !== 0 ? (
              <Delta change={stat.change} lowerIsBetter={stat.lowerIsBetter} />
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}
