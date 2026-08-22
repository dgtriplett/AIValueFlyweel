// The ONE URL-addressable thing in a deliberately no-router SPA: a KB article.
//
// WHY THIS EXCEPTION EXISTS
// -------------------------
// `App.tsx` states the no-router decision and its reason: every view reads the same
// portfolio and the same filters, so a URL per view buys nothing and costs a full
// remount on every tab change. That reasoning holds for views. It does not hold for
// an article, and the console says why at `console.js:3886-3889` — an article "is
// the thing people paste into Slack", so `#kb/<slug>` "has to survive a reload and
// a cold open". A knowledge base whose articles cannot be linked to is a filing
// cabinet, and the citation is the feature.
//
// WHAT IS DELIBERATELY *NOT* HERE
// -------------------------------
// No router, no route table, no history subscription beyond `popstate`, and no URL
// for anything else. A search term is not worth a URL (the console reached the same
// conclusion) and neither is a folder filter or a tab. The whole surface is:
//
//   read the path ONCE on mount   -> resolve a deep link
//   pushState when an article opens -> make the address real and copyable
//   replaceState when it closes     -> leave no dangling /kb/<slug>
//   popstate                        -> honour back/forward between articles
//
// Paths (`/kb/<slug>`) rather than the console's hash, because `app.py`'s SPA
// catch-all already serves `index.html` for any non-asset path — so a pasted
// `/kb/recloser-coordination` cold-loads the app, and this module resolves it on
// mount. Hashes were the console's only option; it is served as static files with
// no such fallback.

/** The path prefix an article lives under. Matches nothing else in the app. */
const KB_PREFIX = '/kb/'

/**
 * The slug in the CURRENT location, or `null`.
 *
 * Read once on mount — see the note above. Decoded because a pasted link may be
 * percent-encoded, and trailing slashes are tolerated because link shorteners and
 * chat clients add them.
 */
export function slugFromLocation(pathname?: string): string | null {
  const path = pathname ?? (typeof window === 'undefined' ? '' : window.location.pathname)
  if (!path.startsWith(KB_PREFIX)) return null
  const raw = path.slice(KB_PREFIX.length).replace(/\/+$/, '')
  if (!raw) return null
  try {
    return decodeURIComponent(raw) || null
  } catch {
    // A malformed escape (`/kb/%zz`) is a broken link, not a crash. Treated as no
    // deep link at all, so the KB index loads instead of the app failing to mount.
    return null
  }
}

/** The canonical, pasteable URL for an article. */
export function articlePath(slug: string): string {
  return `${KB_PREFIX}${encodeURIComponent(slug)}`
}

/**
 * Put an article in the address bar.
 *
 * `pushState` so Back returns where the reader came from. Guarded against pushing
 * the path already showing: opening the article you are already on (a self
 * reference, or a refetch) would otherwise stack duplicate entries and take
 * several Backs to escape.
 */
export function pushArticle(slug: string): void {
  if (typeof window === 'undefined') return
  const next = articlePath(slug)
  if (window.location.pathname === next) return
  window.history.pushState({ kbSlug: slug }, '', next)
}

/**
 * Drop the article from the address bar when the reader closes it.
 *
 * `replaceState`, NOT `pushState`: closing an article is undoing the open, so it
 * should not add an entry that Back would walk into. And `'/'` rather than the
 * previous path because nothing else in the app is addressable — there is no
 * "previous URL" that means anything.
 */
export function clearArticle(): void {
  if (typeof window === 'undefined') return
  if (!window.location.pathname.startsWith(KB_PREFIX)) return
  window.history.replaceState({}, '', '/')
}
