// The KPI card used by the header strip, the dashboards and the funding detail.

import type { ReactNode } from 'react'

export function StatCard({
  label,
  value,
  sub,
  accent,
  icon,
  muted,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  accent: string
  icon?: ReactNode
  muted?: boolean
}) {
  return (
    <div className="card flex items-center gap-4 border-l-4" style={{ borderLeftColor: accent }}>
      {icon ? (
        <div
          className="w-11 h-11 rounded-card flex items-center justify-center shrink-0"
          style={{ background: `${accent}22`, color: accent }}
        >
          {icon}
        </div>
      ) : null}
      <div className="min-w-0">
        <div className="text-2xl font-bold text-white">{value}</div>
        <div className="text-xs text-navy-400 uppercase tracking-wide">{label}</div>
        {sub ? (
          <div className={`text-xs ${muted ? 'text-navy-600' : 'text-navy-500'}`}>{sub}</div>
        ) : null}
      </div>
    </div>
  )
}
