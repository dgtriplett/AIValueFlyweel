// The small status pills the tables, cards and drawer all share.

import {
  INGESTION_LABELS,
  PHASE_COLORS,
  PHASE_LABELS,
  READINESS_LABELS,
  STATUS_COLORS,
  STATUS_LABELS,
} from '../constants'
import type { IngestionStatus, PendingPrereq, Readiness, Status } from '../types'

/**
 * Readiness, with `awaiting_prerequisites` given its own treatment.
 *
 * That state is the one customers misread: the data IS ready, so a red "blocked"
 * badge is wrong and a green one is worse. It gets the purple inline style, the
 * count of what it is waiting on, and a "details" affordance so the tooltip
 * naming the prerequisites is discoverable rather than hidden behind a hover
 * nobody tries. `whitespace-nowrap` keeps the long label on one line inside a
 * table cell — wrapped, it pushed every row to double height.
 */
export function ReadinessBadge({
  readiness,
  pendingPrereqs,
}: {
  readiness?: Readiness | null
  pendingPrereqs?: PendingPrereq[] | null
}) {
  if (!readiness) return null

  if (readiness === 'awaiting_prerequisites') {
    const titles = (pendingPrereqs ?? []).map((prereq) => prereq.title)
    const tooltip = titles.length
      ? `Data is ready. Required prerequisites: ${titles.join(', ')}`
      : 'Data is ready, but prerequisite use cases must be built first.'
    return (
      <span
        data-gaCustomerVisibility="1"
        className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
        style={{
          background: 'rgba(124,107,255,0.18)',
          color: '#B3A7FF',
          border: '1px solid rgba(124,107,255,0.45)',
        }}
        title={tooltip}
      >
        {READINESS_LABELS[readiness]}
        {titles.length ? (
          <span className="text-[10px] opacity-80">{`(${titles.length})`}</span>
        ) : null}
        {titles.length ? (
          <span className="text-[10px] underline decoration-dotted">details</span>
        ) : null}
      </span>
    )
  }

  const className =
    readiness === 'shovel_ready'
      ? 'badge-low'
      : readiness === 'nearly_ready'
        ? 'badge-high'
        : 'badge-critical'
  return <span className={className}>{READINESS_LABELS[readiness]}</span>
}

export function StatusBadge({ status }: { status?: Status | null }) {
  if (!status) return <span className="badge-muted">—</span>
  const color = STATUS_COLORS[status] ?? '#618794'
  return (
    <span
      className="text-xs font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ color, borderColor: `${color}66`, background: `${color}22` }}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}

/**
 * Phase badge — kept for the internal dashboards heatmap only.
 *
 * Phase is derived from prerequisite depth, and showing a customer "Phase 3"
 * reads as a delivery commitment that the derivation cannot support. It is
 * therefore absent from the portfolio table, the filters and the drawer; see
 * `tests/test_console_nav.py::test_phase_is_not_customer_visible_in_the_spa`.
 */
export function PhaseBadge({ phase }: { phase?: number | null }) {
  if (phase == null) return <span className="badge-muted">—</span>
  const color = PHASE_COLORS[phase] ?? '#618794'
  return (
    <span
      className="text-xs font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ color, borderColor: `${color}66`, background: `${color}22` }}
      title={PHASE_LABELS[phase]}
    >
      {`Phase ${phase}`}
    </span>
  )
}

export function IngestionBadge({ status }: { status?: IngestionStatus | null }) {
  if (!status) return <span className="badge-muted">—</span>
  const className =
    status === 'governed'
      ? 'badge-low'
      : status === 'curated'
        ? 'badge-medium'
        : status === 'landed'
          ? 'badge-high'
          : 'badge-muted'
  return <span className={className}>{INGESTION_LABELS[status] ?? status}</span>
}
