// Small, local UI preferences — the ones that belong to this browser, not to the
// account or the server.
//
// These are view-level "how I like to look at my portfolio" choices: whether the
// Kanban board is even on offer, and whether the AI recommendations start
// expanded. A full admin-portal setting is a later phase; a localStorage
// preference is the right scope for a per-user, per-device toggle now.
//
// localStorage access is GUARDED the same way `account.ts` guards it: a browser
// that blocks storage (Safari private mode, cookie-blocking extensions) THROWS on
// read/write rather than returning null. A preference that cannot be read falls
// back to its default, and a write that cannot land is dropped silently — a UI
// preference is never worth taking down a render for.

/** Whether the Kanban board is offered at all. Default TRUE — see PortfolioView. */
export const KANBAN_ENABLED_KEY = 'gridatlas.kanbanEnabled'

/** Whether the AI-recommendations panel starts expanded. Default FALSE (collapsed). */
export const AI_RECS_OPEN_KEY = 'gridatlas.aiRecsOpen'

/**
 * Read a boolean preference, returning `fallback` when it is unset, unreadable,
 * or stored as anything other than the literal 'true'/'false' this module writes.
 *
 * The strict parse matters: a half-written or foreign value must resolve to the
 * DEFAULT, not to `false`. "Kanban is on unless the user turned it off" is only
 * true if an absent or garbled key reads as the default rather than as off.
 */
export function readBoolPref(key: string, fallback: boolean): boolean {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(key)
  } catch {
    return fallback
  }
  if (raw === 'true') return true
  if (raw === 'false') return false
  return fallback
}

/**
 * Persist a boolean preference. A storage failure is swallowed: the in-memory
 * state still updated, so the current session behaves; only the persistence is
 * lost, which is the correct trade for a cosmetic preference.
 */
export function writeBoolPref(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? 'true' : 'false')
  } catch {
    // A UI preference is never worth throwing out of a click handler for.
  }
}
