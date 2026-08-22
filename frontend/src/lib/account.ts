// The selected account, and the one header that carries it.
//
// Multi-tenant scoping lives here rather than at each call site. The operator
// console learned this the hard way and says so at `console.js:61-70`: scoping
// that depends on every fetch remembering to add a header "fails silently and
// shows one customer another's numbers". A missing header is not an error — the
// server falls back to the default account — so the bug is invisible until two
// tenants compare screens.
//
// The SPA previously sent this header NOWHERE, so on a multi-account instance it
// always read the default account while the console read the selected one. The
// interceptor in `api.ts` is the fix; this module is the single definition of
// what it reads and what it writes.

/** The localStorage key the console writes on account switch (`console.js:3231`). */
export const ACCOUNT_STORAGE_KEY = 'avf_account_id'

/** The header `server/accounts.py:19-22` reads before falling back to the default. */
export const ACCOUNT_HEADER = 'X-Grid-Atlas-Account'

/**
 * The selected account id, or `null` when none is chosen.
 *
 * Returns `null` — never `''` — for the absent case, so callers can treat "no
 * account selected" as one condition. An empty or whitespace-only value is
 * treated as absent too: sending `X-Grid-Atlas-Account: ` would be a header the
 * server has to parse rather than a header it can ignore.
 *
 * localStorage access is guarded because it throws, not returns null, when a
 * browser blocks storage (Safari private mode, cookie-blocking extensions). An
 * unreadable account is the same situation as an unset one — use the default —
 * so a throw here must never take down every request in the app.
 */
export function accountId(): string | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(ACCOUNT_STORAGE_KEY)
  } catch {
    return null
  }
  const trimmed = raw?.trim()
  return trimmed ? trimmed : null
}

/**
 * `{ 'X-Grid-Atlas-Account': id }` when an account is selected, else `{}`.
 *
 * Spreadable into a `fetch` init for the call sites axios cannot reach — the
 * multipart upload and the anchor-less blob downloads. Returning an empty object
 * rather than an object with an undefined value keeps `fetch` from sending the
 * header at all when nothing is selected.
 */
export function accountHeaders(): Record<string, string> {
  const id = accountId()
  return id ? { [ACCOUNT_HEADER]: id } : {}
}
