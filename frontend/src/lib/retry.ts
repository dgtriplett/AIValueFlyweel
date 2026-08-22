// When to try again after a failure, and when trying again is the bug.
//
// THE PREMISE THIS MODULE CORRECTS
// --------------------------------
// §4.4 of TIER3_MIGRATION_PLAN.md says to "set react-query `retry: false` for
// these mutations, so a client retry storm doesn't compound a limit". Reading the
// installed react-query (v5.101.4) rather than assuming, mutations already do
// that: `query-core/build/modern/mutation.js:83` resolves `this.options.retry ?? 0`
// — a mutation that fails does NOT retry unless someone opts in. So per-mutation
// `retry: false` is a no-op restating the default, which is worse than nothing: it
// implies the surrounding mutations are unprotected when they are.
//
// The real exposure is QUERIES. `retryer.js:86` resolves `config.retry ?? 3` with
// `defaultRetryDelay` = `min(1000 * 2**n, 30_000)`. `main.tsx` never set `retry`,
// so every GET that 429s is retried three more times — four requests against an
// endpoint that just said "too many requests", at 1s/2s/4s, per mounted query.
// That is the compounding the plan warns about, and it lives on the read path.
//
// So the policy is: retry transient failures (network, 5xx), never retry a
// deliberate refusal (any 4xx, and 429 above all). Installed once as a QueryClient
// default in `main.tsx` rather than per call site, for the same reason the account
// header is an interceptor — a rule enforced by remembering is not enforced.

import { isApiError } from './errors'

/** How many times a genuinely transient failure is worth retrying. */
const MAX_ATTEMPTS = 2

/**
 * Is this failure worth another request?
 *
 * `false` for every 4xx: the server understood and declined, so an identical
 * retry gets an identical refusal. 429 is the case that matters most — retrying
 * spends the budget the `Retry-After` was asking us to wait out, and can push a
 * soft limit into a longer one. 403 and 409 are equally pointless to repeat:
 * permission will not appear, and a consumed confirm token will not un-consume.
 *
 * `true` for a 5xx or a request that never got a response (`status === null`,
 * i.e. offline or DNS failure), which is the case retries exist for.
 */
export function isRetriable(error: unknown): boolean {
  if (!isApiError(error)) {
    // A non-`ApiError` escaped the interceptor — a bug in a queryFn rather than an
    // HTTP failure. Retrying cannot fix a TypeError, and doing so three times just
    // triples the noise in the console.
    return false
  }
  if (error.status == null) return true
  if (error.status >= 400 && error.status < 500) return false
  return error.status >= 500
}

/**
 * The shared `retry` predicate for react-query.
 *
 * Signature is react-query's own (`failureCount, error`), so it drops straight
 * into `defaultOptions.queries.retry` and into any individual query that wants it.
 */
export function retryPolicy(failureCount: number, error: unknown): boolean {
  return failureCount < MAX_ATTEMPTS && isRetriable(error)
}

/**
 * For a mutation on a rate-limited endpoint (`chat`, `research`, `generate`,
 * `sweep`, `write`, `confirm` — `server/limits.py:129-145`).
 *
 * Spread into `useMutation` when you want the intent stated at the call site:
 *
 * ```ts
 * const generate = useMutation({ mutationFn: api.generateUseCases, ...NO_RETRY })
 * ```
 *
 * This is deliberately the react-query default value, not a change to it. It
 * exists so a reader of a limited-endpoint mutation sees the guarantee spelled out
 * instead of having to go read `mutation.js` to learn that `?? 0` is the default —
 * and so that a future `defaultOptions.mutations.retry` cannot silently turn these
 * specific writes into retrying ones. A confirm token is single-use, so an
 * automatic second POST is not a retry, it is a second attempt to spend a spent
 * token: guaranteed 409, and it overwrites the real error with a confusing one.
 */
export const NO_RETRY = { retry: 0 } as const
