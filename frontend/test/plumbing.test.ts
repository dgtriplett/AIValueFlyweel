// Unit tests for the Tier-3 Phase-1 plumbing.
//
// WHAT THESE RUN AGAINST, AND WHY IT MATTERS
// ------------------------------------------
// These import the REAL modules — `src/api.ts` with its interceptors attached,
// `src/lib/errors.ts`, `src/lib/confirm.ts`, `src/lib/retry.ts` — rather than
// reimplementing their logic. A test that restates the code it is testing passes
// when the code is wrong, and the two claims most worth pinning here (the account
// header is injected on EVERY request; a 429 arrives as an `ApiError` carrying
// `Retry-After`) are exactly the claims a reimplementation would fake.
//
// Axios is exercised through its `adapter` option, which replaces the transport
// while leaving the interceptor chain intact. That is what makes "the interceptor
// injects the header" a real assertion: the header is read off the config axios
// actually built, after the interceptor ran.
//
// HOW IT RUNS — see `scripts/check_frontend_tests.py`
// --------------------------------------------------
// No test framework: `assert` from node's stdlib, and a ~20-line runner at the
// bottom. This is deliberate and the constraint is CI's. `.github/workflows/ci.yml`
// pins node 20 and installs NO npm packages, so anything requiring vitest or
// node-22 type-stripping cannot run there. esbuild (a vite dependency, present
// after `npm install`) bundles this to CJS and node runs it — so the gate is green
// on a laptop and honestly SKIPPED in CI, per check.py's "a missing tool is SKIP,
// never PASS" rule. It is not counted as coverage where it did not run.

import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'

import { http } from '../src/api'
import { ACCOUNT_HEADER, ACCOUNT_STORAGE_KEY, accountHeaders, accountId } from '../src/lib/account'
import { ApiError, isLimited, messageOf, parseRetryAfter, retryHint } from '../src/lib/errors'
import {
  applyFailureMessage,
  applyFailurePhase,
  canApprove,
  canRetry,
  isTerminal,
  isUsable,
  phaseOf,
  reasonOf,
} from '../src/lib/confirm'
import { NO_RETRY, isRetriable, retryPolicy } from '../src/lib/retry'
import {
  AI_RECS_OPEN_KEY,
  KANBAN_ENABLED_KEY,
  readBoolPref,
  writeBoolPref,
} from '../src/lib/prefs'
import { articlePath, slugFromLocation } from '../src/lib/kbroute'
import {
  parseInline,
  parseMarkdown,
  slugifyWikiTarget,
  wikiSlugs,
} from '../src/lib/markdown'
import { MAX_ATTACHMENT_BYTES, rejectionOf } from '../src/components/FileDrop'
import { setAccountId } from '../src/views/AccountsView'
import { logoRejection } from '../src/views/BrandingView'
import { parseTags } from '../src/views/ArticleEditor'
import { splitExcerpt } from '../src/views/KnowledgeView'
import { downloadOnboardingTemplate, templateFilename } from '../src/views/OnboardingView'
import { canonicalOptions } from '../src/views/SourceMappingView'
import { classifySummary, dimensionLabel, rankedValues } from '../src/views/TaxonomyView'
import { draftToRule, testSummary } from '../src/views/RulesView'
import { generationConfirmation } from '../src/views/GenerateView'
import { proposalGenerationConfirmation } from '../src/views/ProposalsView'
import { parseRoadmapPackage, roadmapImportConfirmation } from '../src/views/RoadmapImportView'
import { ASSUMPTION_INVALIDATION_KEYS } from '../src/hooks/useAssumptionInvalidation'
import type { ConfirmCardData } from '../src/types'

// ---------------------------------------------------------------------------
// Test doubles
// ---------------------------------------------------------------------------

/**
 * A localStorage stand-in. `src/lib/account.ts` reads the global, and the point of
 * `throws` is that a browser blocking storage THROWS rather than returning null —
 * the case the guard in `accountId()` exists for.
 */
function installStorage(value: string | null, options: { throws?: boolean } = {}) {
  const store = new Map<string, string>()
  if (value !== null) store.set(ACCOUNT_STORAGE_KEY, value)
  ;(globalThis as Record<string, unknown>).localStorage = {
    getItem(key: string) {
      if (options.throws) throw new Error('storage is blocked')
      return store.get(key) ?? null
    },
    setItem(key: string, next: string) {
      store.set(key, next)
    },
    removeItem(key: string) {
      store.delete(key)
    },
  }
}

/** Captures the config axios built, so a test can assert on the real headers. */
function capturingAdapter(response: { status?: number; data?: unknown; headers?: Record<string, string> } = {}) {
  const seen: { headers: Record<string, string>; url?: string; method?: string }[] = []
  const adapter = (config: Record<string, unknown>) => {
    const raw = config.headers as { toJSON?: () => Record<string, string> } & Record<string, string>
    const headers = typeof raw?.toJSON === 'function' ? raw.toJSON() : { ...raw }
    seen.push({ headers, url: config.url as string, method: config.method as string })
    const status = response.status ?? 200
    const result = {
      status,
      statusText: String(status),
      data: response.data ?? {},
      headers: response.headers ?? {},
      config,
    }
    // Axios rejects non-2xx itself only when it built the error; an adapter must
    // do it, which is also what drives the response interceptor under test.
    if (status >= 200 && status < 300) return Promise.resolve(result)
    const error = new Error(`Request failed with status code ${status}`) as Error & Record<string, unknown>
    error.isAxiosError = true
    error.response = result
    error.config = config
    return Promise.reject(error)
  }
  return { adapter, seen }
}

/**
 * A `File` stand-in with a chosen name and size.
 *
 * `rejectionOf` reads only `.name` and `.size`, so this avoids allocating 26MB of
 * real bytes to test the oversize rejection — and avoids depending on `File` being
 * constructible, which varies across the node versions this file has to run under.
 */
function fakeFile(name: string, size: number): File {
  return { name, size } as File
}

/**
 * The rejection message from `draftToRule`, asserting it rejected at all.
 *
 * Narrows the union rather than reaching for `as`, so a draft that unexpectedly
 * VALIDATES fails the test with "expected an error" instead of comparing a regex
 * against `undefined` and passing for the wrong reason.
 */
function errorOf(result: { rule: unknown } | { error: string }): string {
  assert.ok('error' in result, 'expected the draft to be rejected, but it validated')
  return result.error
}

// ---------------------------------------------------------------------------
// 1. The request interceptor injects the account header
// ---------------------------------------------------------------------------

const tests: Record<string, () => Promise<void> | void> = {}

tests['interceptor injects X-Grid-Atlas-Account when an account is selected'] = async () => {
  installStorage('acct-42')
  const { adapter, seen } = capturingAdapter({ data: { ok: true } })
  await http.get('/health', { adapter })
  assert.equal(seen.length, 1)
  assert.equal(seen[0].headers[ACCOUNT_HEADER], 'acct-42')
}

tests['interceptor omits the header entirely when no account is selected'] = async () => {
  installStorage(null)
  const { adapter, seen } = capturingAdapter()
  await http.get('/health', { adapter })
  // Absent, not empty: the server falls back to the default account on a missing
  // header, but has to parse a blank one. Asserting `undefined` rather than
  // falsy so `''` would fail this test.
  assert.equal(seen[0].headers[ACCOUNT_HEADER], undefined)
}

tests['a blank stored account is treated as absent, not sent as an empty header'] = async () => {
  installStorage('   ')
  const { adapter, seen } = capturingAdapter()
  await http.get('/health', { adapter })
  assert.equal(seen[0].headers[ACCOUNT_HEADER], undefined)
}

tests['the header travels on writes too, not just reads'] = async () => {
  installStorage('acct-7')
  const { adapter, seen } = capturingAdapter({ data: {} })
  await http.post('/use-cases', { name: 'x' }, { adapter })
  assert.equal(seen[0].headers[ACCOUNT_HEADER], 'acct-7')
  assert.equal(seen[0].method, 'post')
}

tests['a throwing localStorage does not take down the request'] = async () => {
  // Safari private mode / cookie-blocking extensions throw on getItem. An
  // unreadable account is the same situation as an unset one — use the default —
  // and must never fail every request in the app.
  installStorage('acct-9', { throws: true })
  assert.equal(accountId(), null)
  assert.deepEqual(accountHeaders(), {})
  const { adapter, seen } = capturingAdapter()
  await http.get('/health', { adapter })
  assert.equal(seen[0].headers[ACCOUNT_HEADER], undefined)
}

tests['onboarding export sends the selected account header and server filename'] = async () => {
  // THE REGRESSION THIS EXISTS TO PREVENT
  // ------------------------------------
  // The export is scoped per account server-side (`onboarding.py` reads
  // `accounts.current()`), and a request with no `X-Grid-Atlas-Account` does not
  // fail — `server/accounts.py:19-22` falls back to the DEFAULT account. So a
  // download that skips the header hands one customer another's whole portfolio
  // with no error anywhere. This is the one download in the app where the leaked
  // bytes are the entire model.
  //
  // Phase 9 moved this from a raw `fetch` + hand-added header onto
  // `api.onboardingTemplate()`, so the header now comes from the axios interceptor
  // and the save from the shared `saveBlob`. The assertion is therefore driven
  // through `http.defaults.adapter` — the interceptor chain is intact and the
  // header is read off the config axios actually built, which is what makes this a
  // claim about the shipped code rather than about a reimplementation of it.
  installStorage('acct-export')
  const { adapter, seen } = capturingAdapter({
    data: new Blob(['workbook']),
    headers: { 'content-disposition': 'attachment; filename="tenant-b-onboarding.xlsx"' },
  })

  let clicked = 0
  let removed = 0
  let appended = 0
  let revoked: string | null = null
  const anchor = { href: '', download: '', click: () => { clicked += 1 }, remove: () => { removed += 1 } }
  ;(globalThis as Record<string, unknown>).document = {
    createElement(tag: string) {
      assert.equal(tag, 'a')
      return anchor
    },
    body: {
      appendChild(element: unknown) {
        assert.equal(element, anchor)
        appended += 1
      },
    },
  }
  URL.createObjectURL = () => 'blob:onboarding-template'
  URL.revokeObjectURL = (url: string) => {
    revoked = url
  }

  const restore = http.defaults.adapter
  http.defaults.adapter = adapter
  try {
    await downloadOnboardingTemplate()
  } finally {
    http.defaults.adapter = restore
  }

  assert.equal(seen.length, 1)
  assert.equal(seen[0].url, '/onboarding/export.xlsx')
  assert.equal(seen[0].method, 'get')
  assert.equal(seen[0].headers[ACCOUNT_HEADER], 'acct-export')
  // The server names the file per account; inventing a client-side name would
  // discard the one part of the response that says whose portfolio this is.
  assert.equal(anchor.download, 'tenant-b-onboarding.xlsx')
  assert.equal(anchor.href, 'blob:onboarding-template')
  // Appended before clicking and revoked after: a detached anchor's click is a
  // no-op in Firefox, and an unrevoked object URL leaks the blob for the tab's life.
  assert.equal(appended, 1)
  assert.equal(clicked, 1)
  assert.equal(removed, 1)
  assert.equal(revoked, 'blob:onboarding-template')
}

tests['the export falls back to a filename only when the server sends none'] = () => {
  assert.equal(
    templateFilename('attachment; filename="grid-atlas-onboarding.xlsx"'),
    'grid-atlas-onboarding.xlsx',
  )
  // RFC 5987 wins when present: it is the form that survives a non-ASCII company
  // name, and the server may send both.
  assert.equal(
    templateFilename("attachment; filename=\"fallback.xlsx\"; filename*=UTF-8''Ever%C5%9Dource.xlsx"),
    'Everŝource.xlsx',
  )
  // A malformed escape must not throw — the download is still worth completing.
  assert.equal(templateFilename("attachment; filename*=UTF-8''%zz"), '%zz')
  assert.equal(templateFilename(null), 'onboarding-template.xlsx')
}

tests['the onboarding wizard reaches the API only through the shared client'] = () => {
  // The merged wizard added eleven endpoints, three of them uploads and two of them
  // downloads, which is the largest single-view expansion of the API surface in the
  // migration — and every one is account-scoped. A raw `fetch` here would carry no
  // header (§4.1), so the rule is asserted structurally rather than left to review.
  const source = readFileSync('src/views/OnboardingView.tsx', 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
  assert.doesNotMatch(source, /\bfetch\s*\(/, 'the wizard must not call fetch directly')
  assert.doesNotMatch(source, /accountHeaders\s*\(/, 'the interceptor carries the header now')
  // Both downloads save through the shared helper rather than an anchor href.
  assert.match(source, /saveBlob\(/)
  // Step 5's cross-links are in-app nav, not `/console/#…` hash links.
  assert.doesNotMatch(source, /\/console\/#/)
  assert.match(source, /setTab\(step\.tab\)/)
  // The CSV drop zones must raise the cap, or a valid 40MB extract is rejected
  // client-side before the server ever sees it.
  assert.match(source, /const MAX_INVENTORY_BYTES = 64 \* 1024 \* 1024/)
  // Reconciled to the FileDrop `validate` API: the 64MB cap is now enforced by a
  // custom validator wrapping `rejectionOf`, not a `maxBytes` prop.
  assert.match(source, /maxBytes: MAX_INVENTORY_BYTES/)
}

tests['frontend source has no bare anchor downloads from /api'] = () => {
  // Widened from a single file to the WHOLE source tree in Tier 3 Phase 4.
  //
  // Checking only OnboardingView pinned the one place the rule had already been
  // applied, which is the weakest form of this assertion: it could not fail for new
  // code, and new code is where the mistake happens. The KB has four download and
  // upload surfaces and the console's version of one of them was literally
  // `<a href="/api/kb/attachments/${id}">` (`console.js:2386`), so a regression here
  // is a copy-paste away.
  //
  // An `<a href="/api/…">` bypasses axios and therefore the account interceptor, and
  // a missing account header does NOT raise — `server/accounts.py:19-22` falls back
  // to the default account. So the failure is silent and the symptom is one customer
  // downloading another's document.
  const offenders: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = `${directory}/${entry.name}`
      if (entry.isDirectory()) {
        walk(path)
      } else if (/\.tsx?$/.test(entry.name)) {
        // Comments are stripped FIRST. Several of these files explain the rule by
        // quoting the anti-pattern it forbids — `ArticleView` cites the console's own
        // `<a href="/api/kb/attachments/${id}">` — and a check that cannot tell code
        // from prose punishes writing the explanation down.
        const code = readFileSync(path, 'utf8')
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .replace(/(^|[^:])\/\/.*$/gm, '$1')
        // Matches across newlines, because a multi-line JSX anchor is the common
        // shape and a single-line pattern misses every formatted one. The optional
        // `{` matters more than it looks: the interesting case is an interpolated
        // href (`href={`/api/kb/attachments/${id}`}`), which is precisely what the
        // console had and what a port would reach for. A pattern requiring a quote
        // straight after `=` silently passes the one shape worth catching.
        if (/<a\b[\s\S]*?\bhref=\{?\s*["'`]\/api\//i.test(code)) offenders.push(path)
      }
    }
  }
  walk('src')
  assert.deepEqual(offenders, [], `anchor downloads bypass the account header: ${offenders}`)
}

tests['what-if source enforces and explains server selection caps'] = () => {
  const source = readFileSync('src/views/WhatIfView.tsx', 'utf8')
  assert.match(source, /const MAX_PROJECTION_SOURCES = 12/)
  assert.match(source, /const MAX_COMPARISON_OPTIONS = 6/)
  assert.match(source, /selected\.size < 2 \|\| comparisonLimitExceeded/)
  assert.match(source, /disabled=\{projectionLimitReached && !selected\.has/)
  assert.match(source, /Project up to \$\{MAX_PROJECTION_SOURCES\} sources/)
  assert.match(source, /Comparison is unavailable with \$\{selected\.size\} sources selected/)
}

tests['Header component accepts and renders branding data'] = () => {
  // The regression this prevents: Header.tsx was hardcoded with literal text and
  // never read saved branding, even though the API and save path worked. This
  // asserts structurally that the component accepts branding props and uses them
  // for rendering instead of hardcoded fallbacks.
  const source = readFileSync('src/components/Header.tsx', 'utf8')
  
  // The component must accept a branding prop in its interface
  assert.match(source, /branding\?:\s*HeaderBranding\s*\|\s*null/)
  
  // The component must use branding fields rather than hardcoded strings
  assert.match(source, /branding\?\.display_name/)
  assert.match(source, /branding\?\.subtitle/)
  assert.match(source, /branding\?\.accent_color/)
  assert.match(source, /branding\?\.logo_url/)
  
  // The hardcoded fallbacks should still exist for when branding is not loaded
  assert.match(source, /\?\? ['"]AI Value Flywheel['"]/)
  assert.match(source, /\?\? ['"]Power & Utilities — Data & AI Catalog/)
  assert.match(source, /\?\? ['"]#FF3621['"]/)
  
  // "Powered by Databricks" must NOT be in the header anymore
  assert.doesNotMatch(source, /Powered by Databricks/)
}

tests['App fetches branding on load and passes it to Header'] = () => {
  // The other half of the fix: App.tsx must query the branding API and pass the
  // result to Header, so saved branding actually appears in the UI.
  const source = readFileSync('src/App.tsx', 'utf8')
  
  // Must query branding via the API
  assert.match(source, /useQuery.*\[\s*['"]branding['"]\s*\]/)
  assert.match(source, /api\.branding/)
  
  // Must pass branding to Header component
  assert.match(source, /<Header[^>]*branding=\{branding\}/)
  
  // Footer must contain "Powered by Databricks" in a less prominent location
  assert.match(source, /<footer[\s\S]*Powered by Databricks[\s\S]*<\/footer>/)
}

// ---------------------------------------------------------------------------
// 2. 429 becomes an ApiError carrying Retry-After
// ---------------------------------------------------------------------------

tests['429 arrives as an ApiError with the server detail and Retry-After'] = async () => {
  installStorage(null)
  const { adapter } = capturingAdapter({
    status: 429,
    data: { detail: 'Slow down — wait 12s and try again.' },
    headers: { 'retry-after': '12' },
  })
  const error = await http.get('/chat', { adapter }).then(
    () => null,
    (caught: unknown) => caught,
  )
  assert.ok(error instanceof ApiError, 'expected an ApiError, not an AxiosError')
  assert.equal(error.status, 429)
  assert.equal(error.isRateLimited, true)
  // The server's own words, not a generic fallback — this is the regression that
  // made GeniePanel tell a rate-limited user the feature was broken.
  assert.equal(error.message, 'Slow down — wait 12s and try again.')
  assert.equal(error.rateLimit?.retryAfterSeconds, 12)
  assert.equal(retryHint(error), 'in 12s')
  assert.equal(isLimited(error), true)
}

tests['429 with no Retry-After yields null, not 0'] = async () => {
  installStorage(null)
  const { adapter } = capturingAdapter({ status: 429, data: { detail: 'Too many.' } })
  const error = (await http.get('/chat', { adapter }).catch((e: unknown) => e)) as ApiError
  assert.ok(error instanceof ApiError)
  // "wait, but we don't know how long" is a different statement from "wait 0s",
  // so callers can omit the clause instead of inventing a number.
  assert.equal(error.rateLimit?.retryAfterSeconds, null)
  assert.equal(retryHint(error), null)
}

tests['409 and 403 map to their predicates'] = async () => {
  installStorage(null)
  const conflict = (await http
    .post('/confirm/abcdefgh', undefined, {
      adapter: capturingAdapter({ status: 409, data: { detail: 'This change was already applied.' } }).adapter,
    })
    .catch((e: unknown) => e)) as ApiError
  assert.equal(conflict.isConflict, true)
  assert.equal(conflict.isRateLimited, false)
  assert.equal(conflict.message, 'This change was already applied.')

  const forbidden = (await http
    .get('/accounts', { adapter: capturingAdapter({ status: 403, data: { detail: 'Not an admin.' } }).adapter })
    .catch((e: unknown) => e)) as ApiError
  assert.equal(forbidden.isForbidden, true)
}

tests['a body with no detail falls back to a per-status sentence'] = async () => {
  installStorage(null)
  const error = (await http
    .get('/health', { adapter: capturingAdapter({ status: 500, data: '' }).adapter })
    .catch((e: unknown) => e)) as ApiError
  assert.ok(error instanceof ApiError)
  assert.match(error.message, /server hit an error/i)
  assert.equal(error.detail, null)
}

tests['a 422 validation list is not stringified into [object Object]'] = async () => {
  installStorage(null)
  const error = (await http
    .post('/use-cases', {}, {
      adapter: capturingAdapter({
        status: 422,
        data: { detail: [{ loc: ['body', 'name'], msg: 'field required' }] },
      }).adapter,
    })
    .catch((e: unknown) => e)) as ApiError
  assert.ok(!error.message.includes('[object Object]'))
  assert.equal(error.detail, null)
}

tests['parseRetryAfter handles a delta, an HTTP-date, and junk'] = () => {
  assert.equal(parseRetryAfter('30'), 30)
  assert.equal(parseRetryAfter('0'), 0)
  assert.equal(parseRetryAfter(null), null)
  assert.equal(parseRetryAfter(''), null)
  assert.equal(parseRetryAfter('soon'), null)
  // A proxy may rewrite the delta as a date, per RFC 9110.
  const future = new Date(Date.now() + 45_000).toUTCString()
  const parsed = parseRetryAfter(future)
  assert.ok(parsed !== null && parsed >= 43 && parsed <= 46, `expected ~45, got ${parsed}`)
  // A past date clamps to 0 rather than going negative.
  assert.equal(parseRetryAfter(new Date(Date.now() - 60_000).toUTCString()), 0)
}

tests['messageOf prefers the server sentence and only then the fallback'] = () => {
  assert.equal(messageOf(new ApiError('wait 12s'), 'generic'), 'wait 12s')
  assert.equal(messageOf(new Error('boom'), 'generic'), 'boom')
  assert.equal(messageOf({}, 'generic'), 'generic')
  assert.equal(messageOf(new ApiError(''), 'generic'), 'generic')
}

// ---------------------------------------------------------------------------
// 3. The ConfirmCard state machine
// ---------------------------------------------------------------------------

function card(overrides: Partial<ConfirmCardData> = {}): ConfirmCardData {
  return { token: 'tok-abcdefgh', intent: 'create_use_cases', ...overrides }
}

tests['a live token is ready and approvable'] = () => {
  const live = card({ expires_at: new Date(Date.now() + 60_000).toISOString() })
  assert.equal(isUsable(live), true)
  assert.equal(phaseOf(live), 'ready')
  assert.equal(reasonOf(live), null)
  assert.equal(canApprove('ready'), true)
  assert.equal(isTerminal('ready'), false)
}

tests['an already-consumed token is unusable and says so'] = () => {
  // `GET /confirm/{token}` returns 200 with consumed_at SET rather than an error,
  // so a card restored after a reload can explain itself (server/confirm.py:149).
  const spent = card({ consumed_at: new Date().toISOString() })
  assert.equal(isUsable(spent), false)
  assert.equal(phaseOf(spent), 'unusable')
  assert.equal(reasonOf(spent), 'This change was already applied.')
  assert.equal(canApprove('unusable'), false)
  // No retry offered: the fix is a fresh preview, not another POST.
  assert.equal(canRetry('unusable'), false)
  assert.equal(isTerminal('unusable'), true)
}

tests['an expired token is unusable and distinguishable from a consumed one'] = () => {
  const expired = card({ expired: true })
  assert.equal(phaseOf(expired), 'unusable')
  assert.match(reasonOf(expired) ?? '', /expired/i)
  assert.notEqual(reasonOf(expired), reasonOf(card({ consumed_at: 'now' })))
}

tests['consumed wins over expired when a token is both'] = () => {
  // Both flags can be set on one row. "Already applied" is the more useful fact:
  // the write HAPPENED, and telling the user it expired implies it did not.
  const both = card({ expired: true, consumed_at: new Date().toISOString() })
  assert.equal(reasonOf(both), 'This change was already applied.')
}

tests['409 and 404 on apply are terminal; 429 and 5xx are retriable'] = () => {
  // The distinction the console's four copies got inconsistently.
  assert.equal(applyFailurePhase(new ApiError('applied', { status: 409 })), 'unusable')
  assert.equal(applyFailurePhase(new ApiError('gone', { status: 404 })), 'unusable')
  // A limit leaves the token VALID — this is the one failure where retrying is
  // correct advice, so it must not be filed as terminal.
  assert.equal(applyFailurePhase(new ApiError('slow down', { status: 429 })), 'error')
  assert.equal(applyFailurePhase(new ApiError('boom', { status: 503 })), 'error')
  assert.equal(applyFailurePhase(new Error('offline')), 'error')
  assert.equal(canRetry('error'), true)
  assert.equal(isTerminal('error'), false)
}

tests['applyFailureMessage surfaces the server detail for a 409'] = () => {
  const conflict = new ApiError('This confirmation expired. Ask again to get a fresh one.', {
    status: 409,
  })
  assert.equal(applyFailureMessage(conflict), conflict.message)
  assert.match(applyFailureMessage(new Error('')), /Could not apply this change/)
}

tests['applied and cancelled are terminal and offer nothing'] = () => {
  for (const phase of ['applied', 'cancelled'] as const) {
    assert.equal(isTerminal(phase), true)
    assert.equal(canApprove(phase), false)
    assert.equal(canRetry(phase), false)
  }
}

tests['a rate-limited peek is retriable without being applyable'] = () => {
  // The bug this pins: a failed PEEK lands in `error`, where a retry button is
  // offered. That button must re-READ, not apply — the card never got as far as
  // showing the change, so applying would consume a single-use token to perform a
  // write the user was never shown. `ConfirmCard` tracks which step failed;
  // what is asserted here is the phase contract it relies on.
  const limited = new ApiError('Slow down.', { status: 429 })
  assert.equal(applyFailurePhase(limited), 'error')
  assert.equal(canRetry('error'), true)
  // Crucially NOT approvable: approve is the write, and a 429'd peek means we
  // still do not know whether the token is even usable.
  assert.equal(canApprove('error'), false)
}

tests['approve is offered in exactly one phase'] = () => {
  const phases = ['loading', 'ready', 'applying', 'applied', 'cancelled', 'unusable', 'error'] as const
  assert.deepEqual(phases.filter(canApprove), ['ready'])
  // In particular NOT while applying: the token is single-use, so a second click
  // would spend a token already in flight and get a 409 back.
  assert.equal(canApprove('applying'), false)
  assert.deepEqual(phases.filter(canRetry), ['error'])
}

// ---------------------------------------------------------------------------
// 4. The retry policy
// ---------------------------------------------------------------------------

tests['no 4xx is ever retried, and 429 least of all'] = () => {
  for (const status of [400, 403, 404, 409, 422, 429]) {
    assert.equal(isRetriable(new ApiError('nope', { status })), false, `status ${status}`)
    assert.equal(retryPolicy(0, new ApiError('nope', { status })), false, `status ${status}`)
  }
}

tests['transient failures are retried, up to a bound'] = () => {
  const offline = new ApiError('unreachable', { status: null })
  const server = new ApiError('boom', { status: 503 })
  assert.equal(isRetriable(offline), true)
  assert.equal(isRetriable(server), true)
  assert.equal(retryPolicy(0, server), true)
  assert.equal(retryPolicy(1, server), true)
  // Bounded, so a persistent outage does not become an unbounded request loop.
  assert.equal(retryPolicy(2, server), false)
}

tests['a non-ApiError is not retried'] = () => {
  // A TypeError in a queryFn is a bug, not a transient failure; retrying it three
  // times only triples the console noise.
  assert.equal(isRetriable(new TypeError('x is not a function')), false)
}

tests['NO_RETRY is react-query’s no-retry value'] = () => {
  // Pinned because the whole point is that it matches the library default
  // (`mutation.js:83` resolves `retry ?? 0`); if this drifts, the doc comment in
  // lib/retry.ts becomes a lie.
  assert.equal(NO_RETRY.retry, 0)
}

// ---------------------------------------------------------------------------
// 5. Tier 3 Phase 4 — the knowledge base
// ---------------------------------------------------------------------------
//
// The markdown parser is the piece most worth pinning. It REPLACED the console's
// `renderMarkdown`, whose safety rested on an ordering argument — escape the whole
// source first, then re-introduce a fixed set of constructs — that a later edit
// could quietly break. The replacement's claim is different and stronger: it never
// produces HTML at all, so there is nothing to inject into. These assertions make
// that claim checkable rather than a comment.
//
// The KB modules covered here are the pure ones: the parser, the slug agreement
// with the server, the deep-link resolver and the attachment pre-checks. They need
// no DOM, which is what lets them run under plain node the way this file does.

tests['markdown never emits HTML — angle brackets stay literal text'] = () => {
  const blocks = parseMarkdown('<img src=x onerror=alert(1)>')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'paragraph')
  const spans = blocks[0].kind === 'paragraph' ? blocks[0].spans : []
  assert.equal(spans.length, 1)
  assert.equal(spans[0].kind, 'text')
  assert.equal(spans[0].kind === 'text' ? spans[0].text : '', '<img src=x onerror=alert(1)>')
}

tests['a fenced block is not parsed for markdown, and needs no NUL sentinel'] = () => {
  // The console lifted fences out behind `\0BLOCK0\0` placeholders. Those NUL bytes
  // made `grep` treat console.js as binary and silently return NO matches for any KB
  // symbol — the file read as having no knowledge-base code at all. Here a fence is
  // just a block token, so the sentinel is gone.
  const blocks = parseMarkdown('```python\n**not bold** and [[not a link]]\n```')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'code')
  if (blocks[0].kind !== 'code') return
  assert.equal(blocks[0].language, 'python')
  assert.equal(blocks[0].text, '**not bold** and [[not a link]]')
  assert.ok(!JSON.stringify(blocks).includes('\u0000'), 'parse output must contain no NUL')
}

tests['an unclosed fence takes the rest of the document as code'] = () => {
  // Rather than falling back to prose, where every `*` and `#` in a half-written
  // article would be silently reinterpreted as formatting.
  const blocks = parseMarkdown('intro\n\n```\nstill code\n# not a heading')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[1].kind, 'code')
  assert.equal(blocks[1].kind === 'code' ? blocks[1].text : '', 'still code\n# not a heading')
}

tests['wiki-link slugs match the server slugify for the common case'] = () => {
  // Both sides MUST agree, or an author's `[[Recloser Coordination]]` points at a
  // slug the server never generated and the link is permanently dead.
  assert.equal(slugifyWikiTarget('Recloser Coordination'), 'recloser-coordination')
  assert.equal(slugifyWikiTarget('  Spaced  Out  '), 'spaced-out')
  // Punctuation stripped, per the server's `_SLUG_STRIP = [^\w\s-]`.
  assert.equal(slugifyWikiTarget('ANSI C37.230 (guide)'), 'ansi-c37230-guide')
  // Accented Latin folds to ASCII, matching the server's NFKD-then-ignore.
  assert.equal(slugifyWikiTarget('Réseau'), 'reseau')
  // Capped at MAX_SLUG_LENGTH = 80.
  assert.equal(slugifyWikiTarget('a'.repeat(120)).length, 80)
}

tests['a wiki target that slugifies to nothing is text, not a link'] = () => {
  // An anchor to `/kb/` would look like a reference to a specific article.
  const spans = parseInline('see [[!!!]] here')
  assert.ok(
    spans.every((span) => span.kind === 'text'),
    'a slug-less target must not become a wiki link',
  )
}

tests['a wiki link carries its pipe label but links to the target slug'] = () => {
  const spans = parseInline('[[Recloser Coordination|the coordination rule]]')
  assert.equal(spans.length, 1)
  assert.equal(spans[0].kind, 'wiki')
  if (spans[0].kind !== 'wiki') return
  assert.equal(spans[0].target.slug, 'recloser-coordination')
  assert.equal(spans[0].target.label, 'the coordination rule')
}

tests['an unterminated [[ is not a link'] = () => {
  // Matches `extract_wiki_links` server-side, which requires the closing `]]` for a
  // stated reason: an unterminated one used to swallow the rest of the paragraph and
  // produce a phantom broken link the author could not see the cause of.
  const spans = parseInline('a stray [[ bracket and more text')
  assert.equal(spans.length, 1)
  assert.equal(spans[0].kind, 'text')
}

tests['inline code wins over emphasis inside it'] = () => {
  const spans = parseInline('`**not bold**`')
  assert.equal(spans.length, 1)
  assert.equal(spans[0].kind, 'code')
  assert.equal(spans[0].kind === 'code' ? spans[0].text : '', '**not bold**')
}

tests['consecutive bullets are one list, not one list per line'] = () => {
  const blocks = parseMarkdown('- first\n- second\n- third')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'list')
  if (blocks[0].kind !== 'list') return
  assert.equal(blocks[0].ordered, false)
  assert.equal(blocks[0].items.length, 3)
}

tests['an ordered list is distinguished from a bulleted one'] = () => {
  const blocks = parseMarkdown('1. first\n2. second')
  assert.equal(blocks[0].kind, 'list')
  assert.equal(blocks[0].kind === 'list' ? blocks[0].ordered : false, true)
  assert.equal(blocks[0].kind === 'list' ? blocks[0].items.length : 0, 2)
}

tests['wikiSlugs collects references but ignores fenced code'] = () => {
  // What the reader sees as a link is what should count as a reference; a slug inside
  // a code sample is a code sample. Deduplicated, first-appearance order.
  const slugs = wikiSlugs('see [[Alpha]] and [[Beta]]\n\n```\n[[Gamma]]\n```\n\n[[Alpha]] again')
  assert.deepEqual(slugs, ['alpha', 'beta'])
}

tests['search excerpt markers become highlights, not markup'] = () => {
  // `ts_headline` is configured with `StartSel=<<,StopSel=>>` server-side precisely
  // so the markers survive escaping. The console re-introduced `<mark>` into an
  // escaped HTML string; this splits and hands the pieces to React instead.
  assert.deepEqual(splitExcerpt('the <<recloser>> setting'), [
    { text: 'the ', match: false },
    { text: 'recloser', match: true },
    { text: ' setting', match: false },
  ])
}

tests['a literal << in a body cannot become markup via the excerpt path'] = () => {
  // Only a complete `<<…>>` is a highlight; every piece is a React child either way.
  assert.deepEqual(splitExcerpt('a << b'), [{ text: 'a << b', match: false }])
}

tests['KB deep links resolve a slug from the path, and only from /kb/'] = () => {
  assert.equal(slugFromLocation('/kb/recloser-coordination'), 'recloser-coordination')
  // Trailing slashes are tolerated: chat clients and link shorteners add them.
  assert.equal(slugFromLocation('/kb/recloser-coordination/'), 'recloser-coordination')
  assert.equal(slugFromLocation('/kb/spaced%20slug'), 'spaced slug')
  // The exception is NARROW. Nothing else in the app is addressable, so every other
  // path must resolve to null rather than being treated as a route.
  assert.equal(slugFromLocation('/'), null)
  assert.equal(slugFromLocation('/kb'), null)
  assert.equal(slugFromLocation('/kb/'), null)
  assert.equal(slugFromLocation('/portfolio'), null)
  assert.equal(slugFromLocation('/knowledge/thing'), null)
}

tests['a malformed percent-escape in a deep link is not a crash'] = () => {
  // `decodeURIComponent('%zz')` throws. A broken pasted link must land on the KB
  // index, not stop the app from mounting.
  assert.equal(slugFromLocation('/kb/%zz'), null)
}

tests['articlePath round-trips a slug that needs encoding'] = () => {
  assert.equal(articlePath('recloser-coordination'), '/kb/recloser-coordination')
  assert.equal(slugFromLocation(articlePath('a b')), 'a b')
}

tests['attachment rejection matches the server cap and type list'] = () => {
  // A courtesy check, NOT a gate — `server/knowledge.py` sniffs magic bytes because a
  // filename and a browser Content-Type are both attacker-controlled. But the cap and
  // the extension list are known here, so the obvious mistakes do not cost a round
  // trip and a 422.
  assert.equal(MAX_ATTACHMENT_BYTES, 25 * 1024 * 1024)
  assert.equal(rejectionOf(fakeFile('study.pdf', 1024)), null)
  assert.equal(rejectionOf(fakeFile('sheet.xlsx', 5 * 1024 * 1024)), null)
  assert.match(rejectionOf(fakeFile('empty.pdf', 0)) ?? '', /empty/)
  const tooBig = rejectionOf(fakeFile('huge.pdf', 26 * 1024 * 1024)) ?? ''
  assert.match(tooBig, /26\.0 MB/)
  // Repeats the server's advice rather than just refusing.
  assert.match(tooBig, /Link to it in the article body/)
  assert.match(rejectionOf(fakeFile('script.exe', 10)) ?? '', /not an accepted file type/)
}

tests['existing FileDrop callers keep the 25MB attachment default'] = () => {
  // The props added in Phase 9 are OPTIONAL, and this is the half of that claim
  // worth pinning: `ArticleView` passes neither, so an omitted `maxBytes` must
  // still mean the attachment cap. A default that silently became "unbounded"
  // would turn a courtesy check into a permissive one and only show up as a 413.
  assert.equal(rejectionOf(fakeFile('study.pdf', 24 * 1024 * 1024)), null)
  assert.match(rejectionOf(fakeFile('huge.pdf', 26 * 1024 * 1024)) ?? '', /the limit is 25 MB/)
  assert.match(rejectionOf(fakeFile('extract.csv', 26 * 1024 * 1024)) ?? '', /the limit is 25 MB/)
  // Same file, same call, empty options object — the caller that passes `{}` (a
  // spread of no overrides) must not get different rules from the one that passes
  // nothing at all.
  assert.match(rejectionOf(fakeFile('huge.pdf', 26 * 1024 * 1024), {}) ?? '', /25 MB/)
}

tests['FileDrop honours a raised maxBytes and a narrowed extension list'] = () => {
  // The discovery CSVs accept 64MB server-side (`server/routes/ingestion.py:53`)
  // because a 200k-table estate extracts to ~40MB. Reusing the attachment cap here
  // would reject a VALID file before the request — worse than the round trip the
  // check exists to save, because the user has no way to tell a client refusal from
  // a server one.
  const inventory = { maxBytes: 64 * 1024 * 1024, extensions: ['.csv'] as const }
  assert.equal(rejectionOf(fakeFile('all_tables.csv', 40 * 1024 * 1024), inventory), null)
  // Still bounded — a raised cap is not an absent one.
  const tooBig = rejectionOf(fakeFile('all_columns.csv', 65 * 1024 * 1024), inventory) ?? ''
  assert.match(tooBig, /the limit is 64 MB/)
  // The attachment advice is NOT repeated on a raised cap: telling someone with an
  // oversized `all_columns.csv` to "link to it in the article body" is confident
  // nonsense. The server's own advice there is to split the extract by workspace.
  assert.doesNotMatch(tooBig, /article body/)
  // A narrowed list rejects a type the attachment list accepts, and names what it
  // does accept rather than just refusing.
  const wrongType = rejectionOf(fakeFile('inventory.xlsx', 1024), inventory) ?? ''
  assert.match(wrongType, /not an accepted file type/)
  assert.match(wrongType, /Accepted: \.csv\./)
  // The workbook path takes the other pair: default cap, two extensions.
  const workbook = { extensions: ['.xlsx', '.csv'] as const }
  assert.equal(rejectionOf(fakeFile('filled.xlsx', 1024), workbook), null)
  assert.equal(rejectionOf(fakeFile('filled.csv', 1024), workbook), null)
  assert.match(rejectionOf(fakeFile('notes.pdf', 1024), workbook) ?? '', /not an accepted file type/)
  // Empty still beats every other rule, whatever the caps are — a zero-byte file is
  // a mis-picked file, and reporting its size or type would bury that.
  assert.match(rejectionOf(fakeFile('all_tables.csv', 0), inventory) ?? '', /is empty/)
}

tests['FileDrop passes its limits through to the input and the check'] = () => {
  // The config has to reach BOTH the `accept` attribute and the validator. Wiring
  // only the first gives a picker that hides the file and a drop zone that accepts
  // it; only the second gives the reverse. There is no DOM harness here, so this is
  // asserted on the source — the same technique the what-if caps check uses.
  //
  // Reconciled in the dev merge to the general `accept` + `validate` prop API:
  // callers pass an `accept` string and a `validate` function (typically wrapping
  // `rejectionOf` with the right `FileLimits`) rather than `maxBytes`/`extensions`
  // props. The defaults still fall back to the attachment rules.
  const source = readFileSync('src/components/FileDrop.tsx', 'utf8')
  assert.match(source, /accept = ACCEPTED_EXTENSIONS\.join\(','\)/)
  assert.match(source, /validate = rejectionOf/)
  assert.match(source, /const problem = validate\(file\)/)
  assert.match(source, /accept=\{accept\}/)
}

tests['article tags are split and trimmed, blanks dropped'] = () => {
  assert.deepEqual(parseTags(' protection , ansi c37 ,, '), ['protection', 'ansi c37'])
  assert.deepEqual(parseTags(''), [])
}

// ---------------------------------------------------------------------------
// 6. Tier 3 Phase 5 — the curation writes
//
// These are the first ported screens that CHANGE the estate, and the three claims
// below are each a bug the console shipped rather than a hypothetical:
//
//   - its alias select defaulted to its first option regardless of what the row was
//     already mapped to, so Save on an untouched row silently re-mapped it;
//   - its rule form omitted priority and let a blank `value` submit on any
//     dimension, which 422s for every dimension except `ignore`;
//   - its taxonomy distribution rendered a card per dimension including the empty
//     ones, because `list_taxonomy` seeds all three keys whether or not anything is
//     classified.
//
// Each is invisible on screen until it is wrong against real data, which is exactly
// what makes them worth pinning here.
// ---------------------------------------------------------------------------

tests['canonical options always offer Other, deduped and sorted, with no blanks'] = () => {
  // `Other` is `server/normalize.py`'s sentinel and is valid even when no asset
  // carries it, so it leads the list unconditionally. Nulls come from assets whose
  // source_category was never set.
  assert.deepEqual(
    canonicalOptions(['SCADA', null, 'AMI', 'SCADA', undefined, '']),
    ['Other', 'AMI', 'SCADA'],
  )
  assert.deepEqual(canonicalOptions([]), ['Other'])
  // Not duplicated when the vocabulary already contains it.
  assert.deepEqual(canonicalOptions(['Other', 'GIS']), ['Other', 'GIS'])
}

tests['a rule needs every closed-vocabulary choice made before it can submit'] = () => {
  const base = {
    dimension: '',
    field: '',
    match_type: '',
    pattern: '',
    value: '',
    priority: '100',
    notes: '',
  }
  assert.match(errorOf(draftToRule(base)), /what the rule decides/)
  assert.match(errorOf(draftToRule({ ...base, dimension: 'environment' })), /which field/)
  assert.match(
    errorOf(draftToRule({ ...base, dimension: 'environment', field: 'catalog_name' })),
    /how the pattern/,
  )
  // Whitespace is not a pattern. `validate_rule` rejects it server-side too; the
  // point of checking here is not spending a round trip to be told.
  assert.match(
    errorOf(
      draftToRule({
        ...base,
        dimension: 'environment',
        field: 'catalog_name',
        match_type: 'prefix',
        pattern: '   ',
      }),
    ),
    /needs a pattern/,
  )
}

tests['a blank Assign is only valid on the ignore dimension'] = () => {
  const partial = {
    field: 'catalog_name',
    match_type: 'prefix',
    pattern: 'prod_',
    value: '',
    priority: '10',
    notes: '',
  }
  // Every other dimension has to assign something — a rule that decides
  // `environment` and assigns nothing is not a rule.
  assert.match(errorOf(draftToRule({ ...partial, dimension: 'environment' })), /assign a value/)

  // An ignore rule's effect IS exclusion, so it assigns nothing and `value` must
  // reach the server as null rather than as an empty string it would store.
  const ignore = draftToRule({ ...partial, dimension: 'ignore' })
  assert.ok('rule' in ignore)
  assert.equal(ignore.rule.value, null)
  assert.equal(ignore.rule.dimension, 'ignore')
}

tests['a rule trims its pattern and carries an explicit priority'] = () => {
  // Priority is on the form because FIRST MATCH WINS per dimension: the console
  // omitted it, so every rule landed on the server default of 100 and the tie was
  // broken by insertion order — which is not a convention anybody stated.
  const parsed = draftToRule({
    dimension: 'environment',
    field: 'catalog_name',
    match_type: 'prefix',
    pattern: '  prod_  ',
    value: '  production  ',
    priority: '5',
    notes: '  common convention  ',
  })
  assert.ok('rule' in parsed)
  assert.equal(parsed.rule.pattern, 'prod_')
  assert.equal(parsed.rule.value, 'production')
  assert.equal(parsed.rule.priority, 5)
  assert.equal(parsed.rule.notes, 'common convention')

  // A blank note is absence, not an empty note.
  const unnoted = draftToRule({
    dimension: 'environment',
    field: 'catalog_name',
    match_type: 'prefix',
    pattern: 'dev',
    value: 'development',
    priority: '10',
    notes: '   ',
  })
  assert.ok('rule' in unnoted)
  assert.equal(unnoted.rule.notes, null)
}

tests['a non-integer or negative priority is refused before the round trip'] = () => {
  const base = {
    dimension: 'environment',
    field: 'catalog_name',
    match_type: 'prefix',
    pattern: 'prod',
    value: 'production',
    notes: '',
  }
  assert.match(errorOf(draftToRule({ ...base, priority: 'first' })), /whole number/)
  assert.match(errorOf(draftToRule({ ...base, priority: '1.5' })), /whole number/)
  assert.match(errorOf(draftToRule({ ...base, priority: '-1' })), /whole number/)
  // An empty box is not zero: `Number('')` is 0, which would silently make the
  // rule the highest-priority one in the whole set.
  assert.match(errorOf(draftToRule({ ...base, priority: '' })), /whole number/)
}

tests['a dry run says what it was tested against, and flags what matched nothing'] = () => {
  // `sample_source` is what makes the rest interpretable: rules that work on
  // supplied samples and fail on the estate are the problem the endpoint exists
  // to catch, so the sentence must name which it was.
  const summary = testSummary({
    sample_source: 'discovered_tables',
    rules_applied: 8,
    results: [],
    summary: { total: 100, ignored: 12, unmatched: 30, by_dimension: {} },
  })
  assert.match(summary, /8 rule\(s\) over 100 row\(s\)/)
  assert.match(summary, /from discovered_tables/)
  assert.match(summary, /30 matched nothing/)
}

tests['an unclassified taxonomy dimension renders no distribution card'] = () => {
  // `list_taxonomy` seeds `{d: {} for d in DIMENSIONS}`, so all three keys are
  // always present. An empty one must be `null` — a card with no rows reads as
  // "classified, nothing found" rather than "not classified".
  assert.equal(rankedValues({}), null)
  assert.equal(rankedValues(undefined), null)
  // Highest count first: the distribution is read for its shape, and alphabetical
  // ordering hides it.
  assert.deepEqual(rankedValues({ batch: 3, streaming: 11, cdc: 7 }), [
    ['streaming', 11],
    ['cdc', 7],
    ['batch', 3],
  ])
}

tests['dimension keys are shown as words, not as snake_case'] = () => {
  assert.equal(dimensionLabel('integration_pattern'), 'integration pattern')
  assert.equal(dimensionLabel('criticality'), 'criticality')
}

tests['a classify run that wrote nothing says so in the server’s own words'] = () => {
  // The nothing-to-do path returns `detail` and no counts. Falling through to the
  // count sentence would render "0 classification(s) across 0 asset(s)", which
  // reads as a failure rather than as "there was nothing left to do".
  assert.equal(
    classifySummary({ ok: true, detail: 'Every asset is already classified.' }),
    'Every asset is already classified.',
  )
  assert.match(
    classifySummary({
      ok: true,
      values_written: 84,
      assets_considered: 40,
      batches: 1,
      warnings: [],
    }),
    /84 classification\(s\) written across 40 asset\(s\) in 1 batch\(es\)\./,
  )
}

tests['the curation writes carry the account header and hit the unprefixed rule paths'] =
  async () => {
    // `inventory.py` declares no router prefix, so these are `/api/rules`, not
    // `/api/inventory/rules`. And every one is a WRITE: a missing account header
    // on a write does not read another tenant's data, it MUTATES the default
    // account's — which is why this is asserted per verb rather than once.
    installStorage('acct-curation')

    const patch = capturingAdapter({ data: {} })
    await http.patch('/ingestion/aliases/7', { canonical: 'SCADA' }, { adapter: patch.adapter })
    assert.equal(patch.seen[0].headers[ACCOUNT_HEADER], 'acct-curation')
    assert.equal(patch.seen[0].method, 'patch')

    const post = capturingAdapter({ data: {} })
    await http.post('/rules', { dimension: 'environment' }, { adapter: post.adapter })
    assert.equal(post.seen[0].headers[ACCOUNT_HEADER], 'acct-curation')
    assert.equal(post.seen[0].url, '/rules')

    const del = capturingAdapter({ data: { deleted: true } })
    await http.delete('/rules/3', { adapter: del.adapter })
    assert.equal(del.seen[0].headers[ACCOUNT_HEADER], 'acct-curation')
    assert.equal(del.seen[0].method, 'delete')

    const classify = capturingAdapter({ data: { ok: true } })
    await http.post('/taxonomy/classify', { max_assets: 200 }, { adapter: classify.adapter })
    assert.equal(classify.seen[0].headers[ACCOUNT_HEADER], 'acct-curation')
  }

tests['a 429 on a generate-limited curation write keeps the server’s retry advice'] =
  async () => {
    // `/rules/test` and `/taxonomy/classify` are both `limiter("generate")`. The
    // limit message is written to be shown verbatim, and the views spread
    // `NO_RETRY` so this arrives once rather than four times.
    installStorage('acct-limited')
    const { adapter } = capturingAdapter({
      status: 429,
      data: { detail: 'Too many dry runs. Wait 9s and try again.' },
      headers: { 'retry-after': '9' },
    })
    await assert.rejects(
      http.post('/rules/test', { limit: 100 }, { adapter }),
      (error: unknown) => {
        assert.ok(isLimited(error))
        assert.equal(error.message, 'Too many dry runs. Wait 9s and try again.')
        assert.equal(retryHint(error), 'in 9s')
        // And it must NOT be retriable: spending the budget the Retry-After asked
        // us to wait out can push a soft limit into a longer one.
        assert.equal(isRetriable(error), false)
        return true
      },
    )
  }

// ---------------------------------------------------------------------------
// 7. Tier 3 Phase 6 — token-spending generation and roadmap import
// ---------------------------------------------------------------------------

tests['roadmap packages accept either envelope shape and require use cases'] = () => {
  assert.deepEqual(
    parseRoadmapPackage('{"package":{"useCases":[{"id":"uc-1"}]}}'),
    { useCases: [{ id: 'uc-1' }] },
  )
  assert.deepEqual(
    parseRoadmapPackage('{"use_cases":[{"title":"Grid visibility"}]}'),
    { use_cases: [{ title: 'Grid visibility' }] },
  )
  assert.throws(() => parseRoadmapPackage(''), /Choose a roadmap package/)
  assert.throws(() => parseRoadmapPackage('{nope'), /not valid JSON/)
  assert.throws(() => parseRoadmapPackage('{"useCases":[]}'), /non-empty useCases array/)
}

tests['Phase 6 expensive actions explain the spend before approval'] = () => {
  const generation = generationConfirmation({
    lob_id: null,
    lens: 'ready',
    count: 4,
    time_horizon_bias: null,
    prioritize_regulatory: false,
  })
  assert.match(generation.summary ?? '', /Generate 4 use-case candidates with the LLM/)
  assert.equal(generation.after?.lens, 'ready')

  const proposal = proposalGenerationConfirmation(17)
  assert.match(proposal.summary ?? '', /spends an LLM generation/)
  assert.match(proposal.summary ?? '', /use case 17/)

  const roadmap = roadmapImportConfirmation({
    useCases: { incoming: 5, mapped: 2, toCreateOrTitleMatch: 3 },
  })
  assert.equal(roadmap.intent, 'apply_maturity_roadmap')
  assert.equal(roadmap.after?.incoming, 5)
  assert.equal(roadmap.after?.create_or_match, 3)
}

tests['Phase 6 views use shared safety plumbing and never the console proposal route'] = () => {
  for (const file of ['GenerateView.tsx', 'ProposalsView.tsx', 'RoadmapImportView.tsx']) {
    const source = readFileSync(`src/views/${file}`, 'utf8')
    assert.match(source, /<ConfirmCard/)
    assert.match(source, /NO_RETRY/)
    assert.doesNotMatch(source, /\bfetch\s*\(/)
    assert.doesNotMatch(source, /\/console\/#proposals/)
  }
  const apiSource = readFileSync('src/api.ts', 'utf8')
  for (const endpoint of [
    '/generate/use-cases',
    '/proposals/use-cases/',
    '/sync/maturity-roadmap/preview',
    '/sync/maturity-roadmap/apply',
  ]) {
    assert.match(apiSource, new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(readFileSync('src/components/FileDrop.tsx', 'utf8'), /validate\?: \(file: File\)/)
}

// ---------------------------------------------------------------------------
// Phase 11 — the one assistant, on /api/chat with gated writes
// ---------------------------------------------------------------------------

tests['api.chat posts the message to /api/chat with the account header'] = async () => {
  // The consolidation's whole point: the floating assistant now speaks the
  // tool-calling `/api/chat` loop, not Genie's read-only `/api/genie/ask`. And
  // like every write in the app it must carry the tenant header — a missing one
  // silently falls back to the default account (`server/accounts.py:19-22`), which
  // for a chat that can propose writes would confirm a change against the wrong
  // tenant's estate.
  installStorage('acct-chat')
  const { adapter, seen } = capturingAdapter({
    data: { conversation_id: 'conv_1', answer: 'Here is what I found.', confirm: null },
  })
  const response = await http
    .post('/chat', { message: 'what is my portfolio value?' }, { adapter })
    .then((r) => r.data)
  assert.equal(seen.length, 1)
  assert.equal(seen[0].url, '/chat')
  assert.equal(seen[0].method, 'post')
  assert.equal(seen[0].headers[ACCOUNT_HEADER], 'acct-chat')
  assert.equal(response.answer, 'Here is what I found.')
}

tests['a chat turn that proposes a write comes back with a confirm token'] = async () => {
  // `/api/chat` never applies a write itself: a write tool's proposal becomes a
  // single-use confirm token in `response.confirm` (`server/routes/chat.py`). The
  // assistant panel feeds that straight into the shared <ConfirmCard>, so a
  // chat-proposed change lands in the same human-approval gate as any other and
  // nothing auto-applies.
  installStorage('acct-chat')
  const proposed: ConfirmCardData = card({ intent: 'updateUseCaseStatus', summary: 'Set X to live' })
  const { adapter } = capturingAdapter({
    data: {
      conversation_id: 'conv_2',
      answer: 'I can make that change — confirm below.',
      confirm: proposed,
    },
  })
  const response = await http.post('/chat', { message: 'mark X as live' }, { adapter }).then((r) => r.data)
  assert.ok(response.confirm, 'a proposed write must arrive as a confirm token')
  assert.equal(response.confirm.intent, 'updateUseCaseStatus')
  // A freshly-issued token, no consumed/expired markers, is exactly the `ready`
  // phase <ConfirmCard> gates on — the write waits for approval, it is not applied.
  assert.equal(phaseOf(response.confirm), 'ready')
  assert.equal(canApprove(phaseOf(response.confirm)), true)
}

tests['the assistant panel calls /api/chat, gates writes, and marks the turn NO_RETRY'] = () => {
  // Source-level guarantees for the one assistant. Driven off the source because
  // this repo has no DOM harness (see the ConfirmCard note), and these are exactly
  // the claims that a rewrite could quietly regress:
  //   - it targets the consolidated `/api/chat`, via the account-scoped `api.chat`
  //   - a proposed write is gated through <ConfirmCard>, never auto-applied
  //   - the token-spending turn is NO_RETRY, so a 429 is not double-billed
  //   - it goes through `api.*`, never a raw fetch or an anchor
  const source = readFileSync('src/components/AssistantPanel.tsx', 'utf8')
  assert.match(source, /api\.chat\(/, 'the panel must call the consolidated /api/chat via api.chat')
  assert.doesNotMatch(source, /api\.genieAsk\(/, 'the consolidated assistant no longer calls Genie Q&A')
  assert.match(source, /<ConfirmCard/, 'a chat-proposed write must be gated through the shared ConfirmCard')
  assert.match(source, /NO_RETRY/, 'the token-spending chat turn must not auto-retry a 429')
  assert.doesNotMatch(source, /\bfetch\s*\(/, 'all calls go through the account-scoped http client')
  assert.doesNotMatch(source, /<a\s+[^>]*href=["']\/api/, 'no raw /api anchor bypassing the interceptor')
  // And that api.chat itself hits the right endpoint and nothing re-points it.
  const apiSource = readFileSync('src/api.ts', 'utf8')
  assert.match(apiSource, /chat:\s*\(message: string[\s\S]*?post<ChatResponse>\('\/chat'/, 'api.chat must POST /chat')
}

tests['there is exactly one assistant panel — the Genie duplicate is gone'] = () => {
  // Consolidation is a deletion as much as a rewrite: the old `GeniePanel.tsx` is
  // removed so the app ships ONE assistant behind ONE floating FAB. A lingering
  // second component is the failure this pins.
  const components = readdirSync('src/components')
  assert.ok(components.includes('AssistantPanel.tsx'), 'the consolidated AssistantPanel must exist')
  assert.ok(!components.includes('GeniePanel.tsx'), 'the redundant GeniePanel must be removed')
  const app = readFileSync('src/App.tsx', 'utf8')
  assert.match(app, /<AssistantPanel\s*\/>/, 'App mounts the one AssistantPanel')
  assert.doesNotMatch(app, /GeniePanel/, 'App no longer references the removed GeniePanel')
}

// ---------------------------------------------------------------------------
// 8. Tier 3 Phase 7 — research ↔ assumptions reconciliation
// ---------------------------------------------------------------------------

tests['a manual edit and a recalibration invalidate the SAME KPI query keys'] = () => {
  // The bug this phase fixes: a research recalibration and a manual assumption edit
  // used to invalidate DIFFERENT sets of keys, so a recalibration could leave the
  // dashboard KPIs showing pre-calibration dollars. The fix is ONE definition both
  // consume — `hooks/useAssumptionInvalidation.ts`. This pins that set to exactly
  // the four keys `AssumptionsView` has always invalidated, so a change to it is a
  // deliberate, reviewed edit rather than a silent divergence.
  assert.deepEqual(
    ASSUMPTION_INVALIDATION_KEYS.map((key) => [...key]),
    [['assumptions'], ['portfolio-value'], ['use-cases'], ['blast']],
  )
}

tests['both assumption-change paths invalidate THROUGH the shared hook, not inline'] = () => {
  // Source-level because the hook is a React hook and this suite has no DOM/React
  // harness (see the file header). The guarantee that matters is that NEITHER view
  // hand-rolls its own `invalidateQueries` list for these keys — that is exactly
  // how the console's copies drifted apart. Each must route through the one hook.
  for (const file of ['AssumptionsView.tsx', 'ResearchView.tsx']) {
    const source = readFileSync(`src/views/${file}`, 'utf8')
    assert.match(
      source,
      /useAssumptionInvalidation/,
      `${file} must invalidate through the shared hook`,
    )
    // No inline invalidation of the KPI keys the hook owns: if a view rebuilt the
    // list itself, the two paths could diverge again. `use-cases` is the tell —
    // it is the key a naive port forgets, and finding it in an inline
    // `invalidateQueries` here means the single-source-of-truth was bypassed.
    assert.doesNotMatch(
      source,
      /invalidateQueries\([^)]*\[\s*['"]use-cases['"]/,
      `${file} must not invalidate 'use-cases' inline — route it through the hook`,
    )
  }
}

tests['Research is reachable in the Value nav group and has a render case'] = () => {
  // Nav reachability: `research` must be declared as a TabId, listed in the Value
  // group, and rendered by App.tsx — not left on the ComingSoon placeholder. The
  // structural TabId↔render-case parity is `scripts/check_tab_render.py`; this
  // pins the SEMANTIC placement the plan calls for (research lives under Value).
  const header = readFileSync('src/components/Header.tsx', 'utf8')
  assert.match(header, /\|\s*'research'/, "'research' must be a TabId")
  assert.match(
    header,
    /label:\s*'Value',\s*ids:\s*\[[^\]]*'research'[^\]]*\]/,
    "'research' must sit in the Value nav group",
  )
  const app = readFileSync('src/App.tsx', 'utf8')
  assert.match(app, /case 'research':\s*\n\s*return <ResearchView \/>/,
    'App.tsx must render ResearchView for the research tab')
  // And the placeholder must be GONE for research — a lingering ComingSoon would
  // still satisfy check_tab_render.py while shipping an empty tab.
  assert.doesNotMatch(app, /case 'research':\s*\n\s*return <ComingSoon/)
}

tests['ResearchView uses shared safety plumbing and never a bare fetch or anchor'] = () => {
  // The same guarantees Phase 6's views carry: the confirm gate for the write, and
  // NO_RETRY on the token-spending calls so a 429 is not silently replayed against
  // the research budget. The anchor/fetch bans are also enforced tree-wide above;
  // asserting them here fails with THIS view named, which is faster to act on.
  const source = readFileSync('src/views/ResearchView.tsx', 'utf8')
  assert.match(source, /<ConfirmCard/)
  assert.match(source, /NO_RETRY/)
  assert.doesNotMatch(source, /\bfetch\s*\(/, 'no raw fetch — use the account-scoped api.*')
  assert.doesNotMatch(
    source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1'),
    /<a\b[\s\S]*?\bhref=\{?\s*["'`]\/api\//i,
    'no bare anchor download from /api — it bypasses the account interceptor',
  )
}

tests['the research endpoints go through the account-scoped client'] = async () => {
  // Every research call is a per-account write or read: `server/routes/research.py`
  // reads `accounts.current()` on the profile, the proposals, and the apply. A
  // request without `X-Grid-Atlas-Account` does NOT fail — it silently researches
  // or recalibrates against the DEFAULT account — so the header must ride every one.
  installStorage('acct-research')

  const company = capturingAdapter({ data: { run_id: 1, company: {}, assumptions: [] } })
  await http.post(
    '/research/company',
    { company_name: 'Eversource Energy', calibrate_assumptions: true, propose_lobs: true },
    { adapter: company.adapter },
  )
  assert.equal(company.seen[0].headers[ACCOUNT_HEADER], 'acct-research')
  assert.equal(company.seen[0].method, 'post')
  assert.equal(company.seen[0].url, '/research/company')

  const proposals = capturingAdapter({ data: { run_id: 1, assumptions: [], summary: {} } })
  await http.get('/research/assumptions', { adapter: proposals.adapter })
  assert.equal(proposals.seen[0].headers[ACCOUNT_HEADER], 'acct-research')

  const apply = capturingAdapter({ data: { token: 't', intent: 'apply_research', assumptions: [] } })
  await http.post('/research/apply', { run_id: 1, keys: ['x'] }, { adapter: apply.adapter })
  assert.equal(apply.seen[0].headers[ACCOUNT_HEADER], 'acct-research')
  assert.equal(apply.seen[0].url, '/research/apply')

  const agent = capturingAdapter({ data: { assumption_refinements: [], app_enhancements: [] } })
  await http.get('/agents/customer-enhancements', { adapter: agent.adapter })
  assert.equal(agent.seen[0].headers[ACCOUNT_HEADER], 'acct-research')
}

tests['the research api surface hits the documented endpoints'] = () => {
  const apiSource = readFileSync('src/api.ts', 'utf8')
  for (const endpoint of [
    '/research/company',
    '/research/assumptions',
    '/research/apply',
    '/agents/customer-enhancements',
  ]) {
    assert.match(apiSource, new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
}

// ---------------------------------------------------------------------------
// 9. Tier 3 Phase 8 — the Settings surfaces (Accounts, Admin, Branding)
//
// These are operator screens, and the two facts most worth pinning are the ones a
// reimplementation would fake: every WRITE carries the account header (a missing
// one mutates the DEFAULT account's data, §4.1), and the destructive admin
// operations go through the shared ConfirmCard + NO_RETRY rather than reinventing a
// confirm. The admin authz is SERVER-SIDE (`require_admin`), so nothing here asserts
// a client-side gate — only that a 403 is surfaced, not swallowed into a broken page.
// ---------------------------------------------------------------------------

tests['Phase 8 settings views use shared plumbing and never a raw fetch or console link'] = () => {
  for (const file of ['AccountsView.tsx', 'AdminView.tsx', 'BrandingView.tsx']) {
    const source = readFileSync(`src/views/${file}`, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1')
    // The interceptor carries the account header; a raw fetch would bypass it.
    assert.doesNotMatch(source, /\bfetch\s*\(/, `${file} must not call fetch directly`)
    // No cross-links back into the old console router.
    assert.doesNotMatch(source, /\/console\/#/, `${file} must not link into the console`)
  }
  // The two destructive admin operations are confirm-gated with the shared card and
  // never auto-replayed.
  const admin = readFileSync('src/views/AdminView.tsx', 'utf8')
  assert.match(admin, /<ConfirmCard/)
  assert.match(admin, /NO_RETRY/)
  // The genie space id / space url are the ONE place the admin view renders an
  // external anchor, and it must be a target=_blank workspace link, not a same-tab
  // navigation that throws away the app.
  assert.match(admin, /rel="noopener noreferrer"/)
}

tests['Phase 8 admin routes every server call through the api module'] = () => {
  const apiSource = readFileSync('src/api.ts', 'utf8')
  for (const endpoint of [
    '/demo/status',
    '/demo/load',
    '/demo/reset',
    '/genie/status',
    '/genie/provision',
    '/live/sync-genie',
    '/generate/cleanup',
    '/branding',
    '/branding/logo',
  ]) {
    assert.match(apiSource, new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  // Every one of these is declared on `http` (the account-scoped client), not on a
  // bare axios instance — the interceptor is what scopes them per account.
  const adminMethods = ['demoStatus', 'demoLoad', 'demoReset', 'genieStatus', 'genieProvision']
  for (const method of adminMethods) {
    assert.match(apiSource, new RegExp(`${method}:[\\s\\S]{0,120}?http\\.`), `${method} must use http`)
  }
}

tests['Phase 8 settings views are all wired into the App render switch'] = () => {
  const app = readFileSync('src/App.tsx', 'utf8')
  assert.match(app, /<AccountsView/)
  assert.match(app, /<AdminView/)
  assert.match(app, /<BrandingView/)
  // Their ComingSoon placeholders are gone from the Settings cases.
  assert.doesNotMatch(app, /case 'accounts':\s*return <ComingSoon/)
  assert.doesNotMatch(app, /case 'admin':\s*return <ComingSoon/)
  assert.doesNotMatch(app, /case 'branding':\s*return <ComingSoon/)
}

tests['switching accounts persists the id and the admin writes carry the account header'] =
  async () => {
    // The account SWITCH is the highest-risk item in the phase: it writes the same
    // localStorage key the interceptor reads, so the very next request is scoped to
    // the new account. `setAccountId` is the pure half of that; the view clears the
    // react-query cache around it (asserted structurally below).
    installStorage('acct-old')
    assert.equal(setAccountId(99), true)
    assert.equal(accountId(), '99')

    // And the destructive admin writes are account-scoped: a missing header mutates
    // the DEFAULT account's data, so this is asserted per verb.
    installStorage('acct-admin')

    const load = capturingAdapter({ data: { counts: { use_cases: 42 } } })
    await http.post('/demo/load', undefined, { adapter: load.adapter })
    assert.equal(load.seen[0].headers[ACCOUNT_HEADER], 'acct-admin')
    assert.equal(load.seen[0].method, 'post')
    assert.equal(load.seen[0].url, '/demo/load')

    const reset = capturingAdapter({ data: { counts: { use_cases: 4 } } })
    await http.post('/demo/reset', undefined, { adapter: reset.adapter })
    assert.equal(reset.seen[0].headers[ACCOUNT_HEADER], 'acct-admin')

    const provision = capturingAdapter({ data: { space_id: 'sp-1' } })
    await http.post('/genie/provision', {}, { adapter: provision.adapter })
    assert.equal(provision.seen[0].headers[ACCOUNT_HEADER], 'acct-admin')

    const branding = capturingAdapter({ data: { ok: true, bytes: 2048, mime: 'image/png' } })
    await http.post('/branding/logo', new FormData(), { adapter: branding.adapter })
    assert.equal(branding.seen[0].headers[ACCOUNT_HEADER], 'acct-admin')
  }

tests['the account switch clears the react-query cache rather than invalidating it'] = () => {
  // §4.1: a scoped view holding the PREVIOUS account's rows after the header changed
  // is the tenant-mixing failure the interceptor exists to prevent. `invalidate`
  // keeps stale data on screen while it refetches — the exact window to avoid — so
  // the switch must CLEAR the cache outright.
  const source = readFileSync('src/views/AccountsView.tsx', 'utf8')
  assert.match(source, /queryClient\.clear\(\)/)
  assert.match(source, /setAccountId\(/)
}

tests['a non-admin account list falls back to active-only rather than erroring'] = () => {
  // `include_inactive=true` is admin-gated; a 403 is the DESIGNED answer for a
  // non-admin, so the switcher narrows the request instead of showing a broken page.
  const source = readFileSync('src/views/AccountsView.tsx', 'utf8')
  assert.match(source, /error\.isForbidden/)
  assert.match(source, /api\.accounts\(false\)/)
}

tests['the branding logo rejection matches the server cap and type list'] = () => {
  // A courtesy check, not a gate — the server sniffs magic bytes. But the obvious
  // mistakes (a 5MB photo, a .txt) should not cost a round trip and a 413/422.
  assert.equal(logoRejection(fakeFile('logo.png', 1024)), null)
  assert.equal(logoRejection(fakeFile('logo.svg', 4096)), null)
  assert.match(logoRejection(fakeFile('empty.png', 0)) ?? '', /empty/)
  const tooBig = logoRejection(fakeFile('huge.png', 3 * 1024 * 1024)) ?? ''
  assert.match(tooBig, /the limit is 2 MB/)
  assert.match(logoRejection(fakeFile('doc.txt', 512)) ?? '', /not an accepted image type/)

  // Uploaded through the shared FileDrop with a custom validate, not a bare input.
  const source = readFileSync('src/views/BrandingView.tsx', 'utf8')
  assert.match(source, /MAX_LOGO_BYTES = 2 \* 1024 \* 1024/)
  assert.match(source, /<FileDrop/)
  assert.match(source, /validate=\{logoRejection\}/)
}

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

// Wrapped in a function rather than using top-level await: esbuild targets CJS
// here (axios's node build pulls CJS-only transitive deps that an ESM bundle
// cannot `require`), and CJS has no top-level await.
async function main(): Promise<void> {
  const names = Object.keys(tests)
  let failed = 0

  for (const name of names) {
    try {
      await tests[name]()
      console.log(`  ok   ${name}`)
    } catch (error) {
      failed += 1
      console.log(`  FAIL ${name}`)
      console.log(`       ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  console.log(`\n${names.length - failed}/${names.length} passed`)
  if (failed > 0) process.exit(1)
}

void main()

// ---------------------------------------------------------------------------
// Phase 2 — persona-aware navigation filtering
// ---------------------------------------------------------------------------

tests['admin persona sees all nav groups and all tabs'] = () => {
  // Import the Header module to access the visibleTabsForPersona function
  const { visibleTabsForPersona } = require('../src/components/Header')
  
  const adminTabs = visibleTabsForPersona('admin')
  
  // Admin should see ALL tabs (the full superset)
  const allExpected = [
    'portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'whatif', 'trend',
    'roadmap', 'funding', 'executive',
    'value', 'research',
    'knowledge', 'glossary', 'taxonomy', 'sourcemapping', 'rules', 'artifacts',
    'accounts', 'admin', 'branding',
    'onboarding',
    'generate', 'roadmap_import', 'proposals',
  ]
  
  for (const tab of allExpected) {
    assert.ok(adminTabs.has(tab as any), `admin should see ${tab}`)
  }
}

tests['pm persona sees registry but NOT admin-only curation or settings'] = () => {
  const { visibleTabsForPersona } = require('../src/components/Header')
  
  const pmTabs = visibleTabsForPersona('pm')
  
  // PM SHOULD see:
  const pmExpected = [
    'portfolio', 'flywheel', 'registry', 'dashboards', 'coverage', 'whatif', 'trend',
    'roadmap', 'funding', // NOT executive
    'value', 'research',
    'knowledge', 'glossary', 'artifacts', // NOT sourcemapping, taxonomy, rules
    'onboarding',
    'generate', 'roadmap_import', 'proposals',
  ]
  
  for (const tab of pmExpected) {
    assert.ok(pmTabs.has(tab as any), `pm should see ${tab}`)
  }
  
  // PM should NOT see admin-only tabs
  const pmForbidden = ['sourcemapping', 'taxonomy', 'rules', 'accounts', 'admin', 'branding', 'executive']
  
  for (const tab of pmForbidden) {
    assert.ok(!pmTabs.has(tab as any), `pm should NOT see ${tab}`)
  }
  
  // CRITICAL: pm MUST see 'registry' (data asset management is a PM job)
  assert.ok(pmTabs.has('registry'), 'pm must see registry (data asset management)')
}

tests['executive persona sees only the minimal read-only set'] = () => {
  const { visibleTabsForPersona } = require('../src/components/Header')
  
  const execTabs = visibleTabsForPersona('executive')
  
  // Executive sees ONLY: portfolio, dashboards, roadmap, executive
  const execExpected = ['portfolio', 'dashboards', 'roadmap', 'executive']
  
  assert.equal(execTabs.size, execExpected.length, 
    `executive should see exactly ${execExpected.length} tabs, got ${execTabs.size}`)
  
  for (const tab of execExpected) {
    assert.ok(execTabs.has(tab as any), `executive should see ${tab}`)
  }
  
  // Executive should NOT see anything else
  const execForbidden = [
    'flywheel', 'registry', 'coverage', 'whatif', 'trend',
    'funding', 'value', 'research',
    'knowledge', 'glossary', 'taxonomy', 'sourcemapping', 'rules', 'artifacts',
    'accounts', 'admin', 'branding',
    'onboarding',
    'generate', 'roadmap_import', 'proposals',
  ]
  
  for (const tab of execForbidden) {
    assert.ok(!execTabs.has(tab as any), `executive should NOT see ${tab}`)
  }
}

tests['persona filtering is mutation-worthy: test would fail if pm set is wrong'] = () => {
  const { visibleTabsForPersona } = require('../src/components/Header')
  
  const pmTabs = visibleTabsForPersona('pm')
  
  // This test explicitly checks the BOUNDARY cases that make it mutation-worthy:
  // 1. PM MUST see 'registry' (not admin-only)
  assert.ok(pmTabs.has('registry'), 
    'MUTATION CHECK: pm must see registry — if this fails, the PM set is wrong')
  
  // 2. PM must NOT see 'accounts' (that IS admin-only)
  assert.ok(!pmTabs.has('accounts'), 
    'MUTATION CHECK: pm must NOT see accounts — if this fails, the PM set is wrong')
  
  // 3. PM must NOT see 'rules' (curation is admin-only)
  assert.ok(!pmTabs.has('rules'), 
    'MUTATION CHECK: pm must NOT see rules — if this fails, the PM set is wrong')
  
  // 4. PM must NOT see 'executive' (that's for executives)
  assert.ok(!pmTabs.has('executive'), 
    'MUTATION CHECK: pm must NOT see executive — if this fails, the PM set is wrong')
  
  // 5. Executive must NOT see 'funding' (only PM and admin see that)
  const execTabs = visibleTabsForPersona('executive')
  assert.ok(!execTabs.has('funding'), 
    'MUTATION CHECK: executive must NOT see funding — if this fails, the executive set is wrong')
}


// ---------------------------------------------------------------------------
// Phase 4 — ADMIN LOCKDOWN: enforce admin-only surfaces on TRUSTED isAdmin,
// not on the self-selected persona.
//
// Persona is self-selected and persisted in localStorage, so a NON-admin can
// carry a stale/forced 'admin' persona. The trusted fact is `isAdmin` from
// GET /api/me. These pin the three halves of the lockdown:
//   1. the switcher only OFFERS 'admin' to a trusted admin;
//   2. RoleContext.effectivePersona COERCES a non-admin's 'admin' down to 'pm'
//      centrally, so nav filtering and render agree;
//   3. App.tsx RENDERS a not-authorized panel for admin-only tabs when !isAdmin.
// Each is mutation-worthy: flip the guard and one of these fails.
// ---------------------------------------------------------------------------

tests['effectivePersona coerces a non-admin out of the admin persona to pm'] = () => {
  const { effectivePersona } = require('../src/context/RoleContext')
  // The core lockdown invariant: a stale/forced 'admin' persona held by a
  // non-admin resolves to 'pm' for ALL nav/render purposes.
  assert.equal(effectivePersona('admin', false), 'pm')
  // MUTATION CHECK: an actual admin keeps admin — coercion must not over-reach.
  assert.equal(effectivePersona('admin', true), 'admin')
  // Non-admin personas pass through untouched regardless of isAdmin.
  assert.equal(effectivePersona('pm', false), 'pm')
  assert.equal(effectivePersona('pm', true), 'pm')
  assert.equal(effectivePersona('executive', false), 'executive')
  assert.equal(effectivePersona('executive', true), 'executive')
}

tests['a coerced non-admin resolves to the pm nav set, never the admin superset'] = () => {
  const { effectivePersona } = require('../src/context/RoleContext')
  const { visibleTabsForPersona } = require('../src/components/Header')
  // A non-admin whose stored persona is 'admin' must see EXACTLY the pm tabs.
  const coerced = effectivePersona('admin', false)
  assert.equal(coerced, 'pm')
  const tabs = visibleTabsForPersona(coerced)
  // Sees the PM surfaces...
  assert.ok(tabs.has('registry'), 'coerced non-admin still sees registry (a PM job)')
  assert.ok(tabs.has('portfolio'), 'coerced non-admin sees portfolio')
  // ...but NOT the admin-only ones the stale 'admin' persona would have exposed.
  for (const forbidden of ['accounts', 'admin', 'sourcemapping', 'taxonomy', 'rules', 'branding']) {
    assert.ok(!tabs.has(forbidden as any),
      `MUTATION CHECK: a coerced non-admin must NOT see ${forbidden}`)
  }
}

tests['the persona switcher only OFFERS admin to a trusted admin'] = () => {
  // Source-level, because the switcher is a React component and this suite has no
  // DOM harness (see the file header). The regex asserts the 'admin' option is
  // built behind an isAdmin guard, so a non-admin's option list omits it entirely.
  const source = readFileSync('src/components/Header.tsx', 'utf8')
  // The admin option is spread in ONLY when isAdmin is true.
  assert.match(
    source,
    /\.\.\.\(isAdmin \? \[\{ value: 'admin' as Persona, label: 'Admin' \}\] : \[\]\)/,
    'the admin persona option must be gated on isAdmin',
  )
  // PM and Executive are unconditional — every non-exec-locked user gets those.
  assert.match(source, /\{ value: 'pm', label: 'PM' \}/)
  assert.match(source, /\{ value: 'executive', label: 'Executive' \}/)
  // MUTATION CHECK: the switcher must READ isAdmin from the role context, or the
  // guard above is dead. Without this, a rename of the destructured field passes
  // the regex above while never actually gating.
  assert.match(source, /const \{ activePersona, setPersona, isAdmin, isExecLocked, loading \} = useRole\(\)/)
}

tests['ADMIN_ONLY_TABS names exactly the six admin surfaces — registry is NOT one'] = () => {
  const { ADMIN_ONLY_TABS } = require('../src/components/Header')
  const adminOnly = [...ADMIN_ONLY_TABS].sort()
  assert.deepEqual(
    adminOnly,
    ['accounts', 'admin', 'branding', 'rules', 'sourcemapping', 'taxonomy'],
    'admin-only set must be exactly these six',
  )
  // REFINEMENT / MUTATION CHECK: registry is data-asset management, a PM job — it
  // must NEVER be gated. If a mutation adds it here, this fails.
  assert.ok(!ADMIN_ONLY_TABS.has('registry'),
    'MUTATION CHECK: registry must NOT be admin-only')
  // And a couple of ordinary tabs must not sneak in either.
  assert.ok(!ADMIN_ONLY_TABS.has('portfolio'))
  assert.ok(!ADMIN_ONLY_TABS.has('knowledge'))
}

tests['App.tsx gates every admin-only tab render on isAdmin, and only those'] = () => {
  // Source-level: App is the render shell and there is no DOM harness. The claim
  // is that each admin-only case short-circuits to <NotAuthorized /> when !isAdmin
  // BEFORE returning the real view — so a non-admin who forces/deep-links the tab
  // sees the panel, not admin content. The render CASES stay (the TabId-render
  // gate requires one per TabId); only their CONTENT is gated.
  const source = readFileSync('src/App.tsx', 'utf8')
  const { ADMIN_ONLY_TABS } = require('../src/components/Header')

  for (const tab of ADMIN_ONLY_TABS) {
    // The case still exists (TabId-render-case gate) ...
    assert.match(source, new RegExp(`case '${tab}':`),
      `case '${tab}' must remain so every TabId has a render case`)
    // ... and its content is gated: the isAdmin short-circuit sits inside the case,
    // before the real view is returned.
    assert.match(
      source,
      new RegExp(`case '${tab}':\\s*\\n\\s*if \\(!isAdmin\\) return <NotAuthorized />`),
      `case '${tab}' must render <NotAuthorized /> when !isAdmin`,
    )
  }

  // MUTATION CHECK: a NON-admin tab must NOT carry the guard. 'registry' is the
  // one that matters — gating it would wrongly lock PMs out of data assets.
  assert.doesNotMatch(
    source,
    /case 'registry':\s*\n\s*if \(!isAdmin\)/,
    'MUTATION CHECK: registry must NOT be gated on isAdmin',
  )
  assert.doesNotMatch(
    source,
    /case 'portfolio':\s*\n\s*if \(!isAdmin\)/,
    'portfolio must not be gated',
  )

  // The panel component exists and is distinct/inspectable.
  assert.match(source, /function NotAuthorized\(\)/)
  assert.match(source, /data-ga-not-authorized="1"/)

  // The shell reads the trusted isAdmin from the role context (not persona).
  assert.match(source, /const \{ activePersona, isAdmin \} = useRole\(\)/)
}
// ---------------------------------------------------------------------------
// Phase 5 — Portfolio view: Kanban preference + AI-recs de-emphasis
//
// Two claims from the user feedback, each invisible until it is wrong in front of
// a customer:
//   1. Kanban is ON by default, a preference can turn it off, and turning it off
//      hides the Kanban option and forces the table (src/lib/prefs.ts + the
//      PortfolioView wiring that reads it).
//   2. the AI recommendations are COLLAPSED on the initial landing rather than
//      rendered inline (src/components/AiRecommendationsPanel.tsx).
//
// The pure preference logic is exercised against the real module; the view wiring
// is asserted on source, the same technique the what-if caps and curation-write
// checks above use — there is no DOM harness here.
// ---------------------------------------------------------------------------

/** A general in-memory localStorage that records writes, for the prefs tests. */
function installKvStore(seed: Record<string, string> = {}, options: { throws?: boolean } = {}) {
  const store = new Map<string, string>(Object.entries(seed))
  ;(globalThis as Record<string, unknown>).localStorage = {
    getItem(key: string) {
      if (options.throws) throw new Error('storage is blocked')
      return store.get(key) ?? null
    },
    setItem(key: string, next: string) {
      if (options.throws) throw new Error('storage is blocked')
      store.set(key, next)
    },
    removeItem(key: string) {
      store.delete(key)
    },
  }
  return store
}

tests['kanban is enabled by default when the preference is unset'] = () => {
  // The whole point of feedback item 1: an absent key reads as ON, not OFF. If the
  // parse ever falls through to `false`, a fresh browser would hide the board the
  // user asked to keep on by default.
  installKvStore({})
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
}

tests['a stored kanban=false turns the board off, and =true keeps it on'] = () => {
  installKvStore({ [KANBAN_ENABLED_KEY]: 'false' })
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), false)
  installKvStore({ [KANBAN_ENABLED_KEY]: 'true' })
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
}

tests['a garbled or foreign preference value resolves to the default, not false'] = () => {
  // "on unless turned off" only holds if a half-written or foreign value reads as
  // the DEFAULT. A naive `=== 'true'` parse would silently disable Kanban here.
  installKvStore({ [KANBAN_ENABLED_KEY]: 'yes' })
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
  installKvStore({ [KANBAN_ENABLED_KEY]: '' })
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
}

tests['AI recommendations default to collapsed (open pref defaults false)'] = () => {
  // Feedback item 2: the shelf must be closed on first load. An unset key reads as
  // the collapsed default rather than expanding the block that "can be a lot".
  installKvStore({})
  assert.equal(readBoolPref(AI_RECS_OPEN_KEY, false), false)
  // A returning user who opened it keeps it open.
  installKvStore({ [AI_RECS_OPEN_KEY]: 'true' })
  assert.equal(readBoolPref(AI_RECS_OPEN_KEY, false), true)
}

tests['writeBoolPref round-trips through the store and reads back'] = () => {
  const store = installKvStore({})
  writeBoolPref(KANBAN_ENABLED_KEY, false)
  assert.equal(store.get(KANBAN_ENABLED_KEY), 'false')
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), false)
  writeBoolPref(KANBAN_ENABLED_KEY, true)
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
}

tests['a throwing localStorage falls back to the default and does not blow up'] = () => {
  // Safari private mode / cookie-blocking extensions throw. A cosmetic preference
  // must never take down a render — read returns the default, write is swallowed.
  installKvStore({}, { throws: true })
  assert.equal(readBoolPref(KANBAN_ENABLED_KEY, true), true)
  assert.equal(readBoolPref(AI_RECS_OPEN_KEY, false), false)
  writeBoolPref(KANBAN_ENABLED_KEY, false) // must not throw
}

tests['PortfolioView gates the Kanban option and control on the preference'] = () => {
  const source = readFileSync('src/views/PortfolioView.tsx', 'utf8')
  // Reads the preference, default TRUE — Kanban on unless turned off.
  assert.match(source, /readBoolPref\(KANBAN_ENABLED_KEY, true\)/)
  // The Kanban toggle button only renders when the preference is on.
  assert.match(source, /\{kanbanEnabled && \(/)
  // Disabling forces the table so the user is never stranded on a hidden view.
  assert.match(source, /if \(!next\) setView\('table'\)/)
  assert.match(source, /if \(!kanbanEnabled && view === 'kanban'\) setView\('table'\)/)
  // Even a stale 'kanban' view renders the table when the preference is off.
  assert.match(source, /view === 'table' \|\| !kanbanEnabled \? \(/)
  // A discoverable control labelled for the user.
  assert.match(source, /Show Kanban board/)
}

tests['PortfolioView collapses AI recommendations behind the shelf on landing'] = () => {
  const source = readFileSync('src/views/PortfolioView.tsx', 'utf8')
  // Both AI panels are wrapped in the collapsed shelf rather than rendered inline.
  assert.match(source, /<AiRecommendationsPanel count=\{2\}>/)
  assert.match(source, /<RecommendPanel onOpen=\{onOpen\} \/>/)
  assert.match(source, /<SourceRecommendPanel onOpenUseCase=\{onOpen\} \/>/)
  // The shelf must not be pre-opened: no defaultOpen / open prop forcing it.
  assert.doesNotMatch(source, /<AiRecommendationsPanel[^>]*defaultOpen/)
}

tests['the AI-recs shelf is closed on first load and persists its open choice'] = () => {
  const source = readFileSync('src/components/AiRecommendationsPanel.tsx', 'utf8')
  // Initial state comes from the persisted pref, defaulting to collapsed.
  assert.match(source, /useState\(\(\) => readBoolPref\(AI_RECS_OPEN_KEY, false\)\)/)
  // Toggling writes the choice back so it survives a reload.
  assert.match(source, /writeBoolPref\(AI_RECS_OPEN_KEY, next\)/)
  // The children only mount when open — so the LLM-backed inner queries stay lazy.
  assert.match(source, /\{open && <div/)
}
