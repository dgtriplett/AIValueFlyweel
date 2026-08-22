// Turning HTTP failures into something the UI can say out loud.
//
// Two things go wrong when an app has no error translation layer. The first is
// what `GeniePanel` did before this: catch everything, say "unavailable", and
// tell a rate-limited user the feature is broken. The second is subtler — every
// call site grows its own `error.response?.data?.detail` chain, and they disagree
// about which shapes to check.
//
// So the response interceptor in `api.ts` calls `describeError` once and hands
// the result to whoever catches. The parsing here is deliberately free of axios
// and of the DOM: it takes a status, a header lookup and a body, which makes it
// directly unit-testable and reusable by the raw-`fetch` call sites.

/** How long the server asked us to wait, and why, when a limit is hit. */
export interface RateLimitInfo {
  /** Seconds from `Retry-After`. `null` when the header is absent or unparseable. */
  retryAfterSeconds: number | null
}

/**
 * A normalized HTTP failure.
 *
 * `message` is always safe to render: FastAPI's `detail` when there is one,
 * otherwise a generic fallback. The rate-limit messages in `server/limits.py:191`
 * are written to be shown verbatim ("wait 12s and try again"), which is exactly
 * why the interceptor must stop discarding them.
 */
export class ApiError extends Error {
  readonly status: number | null
  readonly detail: string | null
  readonly rateLimit: RateLimitInfo | null
  /**
   * The underlying failure, kept for debugging.
   *
   * Declared explicitly because `lib` targets ES2020 (`tsconfig.json`), where
   * `Error.cause` is not in the standard type yet. Raising the whole project's
   * target for one field would change emit for every file.
   */
  cause?: unknown

  constructor(
    message: string,
    options: {
      status?: number | null
      detail?: string | null
      rateLimit?: RateLimitInfo | null
      cause?: unknown
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status ?? null
    this.detail = options.detail ?? null
    this.rateLimit = options.rateLimit ?? null
    if (options.cause !== undefined) this.cause = options.cause
  }

  /** A rate limit, i.e. worth offering a retry rather than reporting a fault. */
  get isRateLimited(): boolean {
    return this.status === 429
  }

  /**
   * A token-confirm conflict: the token is real but no longer usable.
   * `server/routes/generate.py:441` returns 409 for already-applied and expired
   * tokens alike, and for the proposals regenerate case (`console.js:2655-2663`).
   */
  get isConflict(): boolean {
    return this.status === 409
  }

  /** Denied by `require_admin`. The server's `detail` is the actionable part. */
  get isForbidden(): boolean {
    return this.status === 403
  }
}

/** A type guard, so `catch (error: unknown)` can narrow without a cast. */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/**
 * FastAPI's error message, if the body carries one.
 *
 * `detail` is a string for `HTTPException(429, detail="...")`, but a list of
 * per-field objects for a 422 validation failure. Only the string form is worth
 * showing a user, so the list form is deliberately ignored rather than
 * stringified into `[object Object]`.
 */
export function detailOf(body: unknown): string | null {
  if (typeof body === 'string') return body.trim() || null
  if (!body || typeof body !== 'object') return null
  const record = body as Record<string, unknown>
  for (const key of ['detail', 'error', 'message']) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

/**
 * `Retry-After` in seconds.
 *
 * The spec allows an HTTP-date as well as a delta in seconds; `limits.py:194`
 * always sends the delta, but a proxy in front of it may not, so the date form
 * is handled rather than silently treated as 0. A past date clamps to 0, and
 * anything unparseable is `null` — "wait, but we don't know how long" is a
 * different statement from "wait 0 seconds".
 */
export function parseRetryAfter(value: string | null | undefined): number | null {
  if (value == null) return null
  const trimmed = value.trim()
  if (!trimmed) return null

  const seconds = Number(trimmed)
  if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds))

  const timestamp = Date.parse(trimmed)
  if (Number.isNaN(timestamp)) return null
  return Math.max(0, Math.ceil((timestamp - Date.now()) / 1000))
}

/** Default text per status, used only when the server sent no `detail`. */
function fallbackMessage(status: number | null): string {
  if (status === 429) return 'Too many requests in a row. Wait a moment and try again.'
  if (status === 409) return 'That is no longer valid. Ask again to get a fresh one.'
  if (status === 403) return 'You do not have permission to do that.'
  if (status === 404) return 'Not found.'
  if (status != null && status >= 500) return 'The server hit an error. Try again.'
  if (status != null) return `Request failed (${status}).`
  return 'Could not reach the server. Check your connection and try again.'
}

/**
 * Normalize any HTTP failure into an `ApiError`.
 *
 * Takes a header *lookup function* rather than a headers object so one
 * implementation serves both axios (whose headers are a plain object, or an
 * `AxiosHeaders` instance) and `fetch` (whose `Headers` needs `.get()`).
 */
export function describeError(input: {
  status?: number | null
  body?: unknown
  header?: (name: string) => string | null | undefined
  cause?: unknown
}): ApiError {
  const status = input.status ?? null
  const detail = detailOf(input.body)
  const rateLimit: RateLimitInfo | null =
    status === 429
      ? { retryAfterSeconds: parseRetryAfter(input.header?.('retry-after')) }
      : null

  return new ApiError(detail ?? fallbackMessage(status), {
    status,
    detail,
    rateLimit,
    cause: input.cause,
  })
}

/**
 * "in 12s" / "in 2m" — the wait, phrased for a sentence.
 * `null` when the server did not say, so callers can omit the clause entirely
 * rather than inventing a number.
 */
export function retryHint(error: ApiError): string | null {
  const seconds = error.rateLimit?.retryAfterSeconds
  if (seconds == null) return null
  if (seconds < 60) return `in ${Math.max(1, seconds)}s`
  return `in ${Math.ceil(seconds / 60)}m`
}

/**
 * The sentence to show for any caught failure, preferring the server's own words.
 *
 * `ApiError.message` is already FastAPI's `detail` when there was one, and the
 * limit messages are written to be shown verbatim. The `fallback` therefore only
 * covers a failure that carried no body — an offline request, or a thrown
 * non-`Error`. Lives here rather than at a call site because every surface that
 * catches needs exactly this and they must not each invent their own phrasing.
 */
export function messageOf(error: unknown, fallback = 'Something went wrong.'): string {
  if (isApiError(error)) return error.message || fallback
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/**
 * Is a rate limit the reason this failed?
 *
 * Narrows to `ApiError` so a caller can pass the result straight to `retryHint`.
 * The distinction callers need it for: a limit means "wait, then this will work",
 * every other error means "this did not work", and those deserve different words.
 */
export function isLimited(error: unknown): error is ApiError {
  return isApiError(error) && error.isRateLimited
}
