// Save a fetched blob to disk.
//
// This is the object-URL half of the console's `downloadApi()`
// (`console.js:92-109`), with the fetch half deleted: the request itself is an
// `api.*` method now, so it travels through the axios instance and picks up the
// account header from the interceptor. The console's version called `fetch`
// directly and re-added the header by hand, which is the pattern §4.1 of
// TIER3_MIGRATION_PLAN.md exists to remove.
//
// Split out rather than inlined because the anchor-click dance is easy to get
// subtly wrong — a link that is never appended does not fire in Firefox, and an
// object URL that is never revoked leaks the whole blob for the life of the tab.

/**
 * Prompt the browser to save `blob` as `filename`.
 *
 * Deliberately does NOT open the content inline. The server sends these with
 * `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`
 * (§4.6), and turning a download into a blob preview would discard that
 * decision — the pack is arbitrary text destined for a customer's deck, not
 * something to render in the app's own origin.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  // Appended before clicking: a detached anchor's click is a no-op in Firefox.
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
