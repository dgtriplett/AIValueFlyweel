// The inline explanation banner the ported console views lean on.
//
// The console's `banner(kind, message)` (`console.js:111-115`) is used ~40 times
// and is load-bearing rather than decorative: several views say the most useful
// thing they have to say in one — "these cells are data you already landed that
// nothing uses", "these needs are unmet everywhere, so they are acquisition
// decisions". Dropping those sentences in the port would lose the interpretation
// and leave a bare table.
//
// It is NOT a toast. `components/Toasts.tsx` reports transient failures the user
// is waiting on; this states a standing fact about what is on screen, so it stays
// in the layout and does not auto-dismiss.

import { CircleAlert, CircleCheck, Info, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'

/** Maps 1:1 onto the console's four banner classes, so parity is checkable. */
export type BannerKind = 'info' | 'ok' | 'warn' | 'err'

// Alpha-composited like the badges in `index.css`: these sit on both the page
// background and inside a `.card`, and an opaque fill reads as a second card.
const STYLES: Record<BannerKind, { background: string; borderColor: string; color: string }> = {
  info: { background: 'rgba(34,114,180,0.12)', borderColor: 'rgba(34,114,180,0.4)', color: '#8ACAFF' },
  ok: { background: 'rgba(0,169,114,0.12)', borderColor: 'rgba(0,169,114,0.4)', color: '#9ED6C4' },
  warn: { background: 'rgba(255,171,0,0.13)', borderColor: 'rgba(255,171,0,0.4)', color: '#FFDB96' },
  err: { background: 'rgba(255,54,33,0.12)', borderColor: 'rgba(255,54,33,0.4)', color: '#FF9E94' },
}

const ICONS: Record<BannerKind, typeof Info> = {
  info: Info,
  ok: CircleCheck,
  warn: TriangleAlert,
  err: CircleAlert,
}

export function Banner({
  kind = 'info',
  children,
}: {
  kind?: BannerKind
  children: ReactNode
}) {
  const Icon = ICONS[kind]
  return (
    <div
      role={kind === 'err' ? 'alert' : undefined}
      className="rounded-md border px-3.5 py-2.5 text-[13px] flex items-start gap-2"
      style={STYLES[kind]}
    >
      <Icon className="w-4 h-4 mt-0.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  )
}

/**
 * The three states every read-only view shares, in the order they can occur.
 *
 * Exists because six ported views each need "loading / couldn't load / nothing
 * here yet" and the console spelled all three differently in every view. Returns
 * `null` once there is data to show, so a caller reads as:
 *
 * ```tsx
 * <QueryState query={q} loading="Building the matrix…" empty={!rows.length} />
 * {rows.length ? <table>…</table> : null}
 * ```
 *
 * The error path deliberately shows the server's own words: `api.ts`'s
 * interceptor has already turned the failure into an `ApiError` whose message is
 * FastAPI's `detail`, and those details name the GRANT to run.
 */
export function QueryState({
  query,
  loading,
  empty,
  emptyMessage,
  errorMessage = "Couldn't load this. Please retry.",
}: {
  query: { isLoading: boolean; isError: boolean; error?: unknown }
  loading: string
  /** Pass the caller's own "there are no rows" test — only read once loaded. */
  empty?: boolean
  emptyMessage?: ReactNode
  errorMessage?: string
}) {
  if (query.isLoading) {
    return (
      <div className="text-sm text-navy-400 py-2" role="status">
        {loading}
      </div>
    )
  }
  if (query.isError) {
    return (
      <Banner kind="err">
        {query.error instanceof Error && query.error.message
          ? query.error.message
          : errorMessage}
      </Banner>
    )
  }
  if (empty) return <Banner kind="info">{emptyMessage ?? 'Nothing to show yet.'}</Banner>
  return null
}
