// Per-persona "last viewed tab" memory — a per-user, per-device UI preference,
// the same scope and the same discipline as `prefs.ts`.
//
// WHY PER PERSONA
// ---------------
// An admin can 'view as' PM or Executive, and each persona has a different natural
// home and a different set of reachable tabs. Remembering ONE last tab across all
// personas would strand a persona on a tab it cannot see (e.g. an admin who left
// 'accounts' open, then switches to view as Executive). Keying the memory by
// persona means each persona returns to where IT left off, and the visibility
// fallback (see `resolveLandingTab` in components/Header.tsx) handles the rest.
//
// localStorage is GUARDED exactly as `prefs.ts` and `account.ts` guard it: a
// browser that blocks storage (Safari private mode, cookie-blocking extensions)
// THROWS on read/write. An unreadable value falls back to the persona's default
// home tab, and a write that cannot land is dropped silently — remembering a tab
// is never worth taking down a render or a click handler for.

import type { Persona } from '../context/RoleContext'
import type { TabId } from '../components/Header'

/** The localStorage key holding a given persona's last-viewed tab. */
export function lastTabKey(persona: Persona): string {
  return `ga:lastTab:${persona}`
}

/**
 * Read a persona's stored last tab, or null when it is unset or unreadable.
 *
 * Returns the raw string (a `TabId | null`); callers MUST still check it is
 * visible to the persona before using it, because a stored tab can go stale (a
 * persona's visible set changes across releases, or a hand-edited value). That
 * check is `resolveLandingTab` — this function only owns the storage read.
 */
export function readLastTab(persona: Persona): TabId | null {
  try {
    const raw = localStorage.getItem(lastTabKey(persona))
    return (raw as TabId | null) ?? null
  } catch {
    return null
  }
}

/** Persist a persona's last-viewed tab. A storage failure is swallowed. */
export function writeLastTab(persona: Persona, tab: TabId): void {
  try {
    localStorage.setItem(lastTabKey(persona), tab)
  } catch {
    // Remembering a tab is never worth throwing out of an effect for.
  }
}
