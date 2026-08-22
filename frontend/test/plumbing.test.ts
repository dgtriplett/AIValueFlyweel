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
import { articlePath, slugFromLocation } from '../src/lib/kbroute'
import {
  parseInline,
  parseMarkdown,
  slugifyWikiTarget,
  wikiSlugs,
} from '../src/lib/markdown'
import { MAX_ATTACHMENT_BYTES, rejectionOf } from '../src/components/FileDrop'
import { parseTags } from '../src/views/ArticleEditor'
import { splitExcerpt } from '../src/views/KnowledgeView'
import { downloadOnboardingTemplate } from '../src/views/OnboardingView'
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
  installStorage('acct-export')
  let request: { input?: string | URL | Request; init?: RequestInit } = {}
  let clicked = 0
  let removed = 0
  let appended = 0
  let revoked: string | null = null
  const anchor = {
    href: '',
    download: '',
    style: { display: '' },
    click() {
      clicked += 1
    },
    remove() {
      removed += 1
    },
  }

  ;(globalThis as Record<string, unknown>).fetch = async (
    input: string | URL | Request,
    init?: RequestInit,
  ) => {
    request = { input, init }
    return new Response(new Blob(['workbook']), {
      status: 200,
      headers: {
        'content-disposition': 'attachment; filename="tenant-b-onboarding.xlsx"',
      },
    })
  }
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

  await downloadOnboardingTemplate()

  assert.equal(request.input, '/api/onboarding/export.xlsx')
  assert.equal((request.init?.headers as Record<string, string>)[ACCOUNT_HEADER], 'acct-export')
  assert.equal(anchor.download, 'tenant-b-onboarding.xlsx')
  assert.equal(anchor.href, 'blob:onboarding-template')
  assert.equal(anchor.style.display, 'none')
  assert.equal(appended, 1)
  assert.equal(clicked, 1)
  assert.equal(removed, 1)
  assert.equal(revoked, 'blob:onboarding-template')
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

tests['article tags are split and trimmed, blanks dropped'] = () => {
  assert.deepEqual(parseTags(' protection , ansi c37 ,, '), ['protection', 'ansi c37'])
  assert.deepEqual(parseTags(''), [])
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
