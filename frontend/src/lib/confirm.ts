// The preview -> commit -> POST /confirm/{token} state machine, without any UI.
//
// Four surfaces share this flow (generate, research, proposals, chat —
// TIER3_MIGRATION_PLAN.md §4.5), and the console implements it four times
// (`console.js` renderConfirm :967, renderChatConfirm :1101, renderApplyCard :1780,
// renderProposal :2675). Those copies disagree about the failure cases, which is
// the actual defect: an expired token and an already-applied token are different
// facts, and a card that says "failed" for both invites the user to retry the one
// where retrying is guaranteed to fail again.
//
// The logic lives here rather than inside the component so the state machine is
// testable without a DOM — `tests/test_frontend_plumbing.py` drives it through
// node, and there is no renderer in this repo to mount a component in.
//
// WHY A TOKEN CAN STOP WORKING, from the server (`server/confirm.py:117-147`):
//   - consume is a single atomic UPDATE guarded on `consumed_at IS NULL AND
//     expires_at > now()`, so exactly one of two racing confirms wins;
//   - the loser gets a 409 whose `detail` already distinguishes the reason
//     ("This change was already applied." vs "This confirmation expired.");
//   - `GET /confirm/{token}` (peek) is different: it returns 200 with
//     `consumed_at` / `expired` SET rather than an error, precisely so a card
//     restored after a page reload can explain itself instead of just failing.
// Both paths therefore have to be read, which is why `phaseOf` looks at the card
// and `applyFailurePhase` looks at the error.

import { isApiError, messageOf } from './errors'
import type { ConfirmCardData } from '../types'

/**
 * What the card should be showing.
 *
 * `unusable` collapses expired and already-applied deliberately: they differ in
 * WORDING, not in what the UI offers, and both mean "the approve button must not
 * be clickable". The distinguishing sentence comes from `reasonOf` / the server's
 * `detail`, so there is one branch for behaviour and one string for explanation.
 */
export type ConfirmPhase =
  /** Peeking the token to find out whether it is still good. */
  | 'loading'
  /** A live token: show before/after and offer approve. */
  | 'ready'
  /** POST in flight. The approve button must be disabled — the token is single-use. */
  | 'applying'
  /** The write succeeded. Terminal. */
  | 'applied'
  /** The user declined. Terminal, and nothing was written. */
  | 'cancelled'
  /** Expired, already consumed, or never issued. Terminal; approve is not offered. */
  | 'unusable'
  /** A transient failure (5xx, offline). Retrying IS worth offering. */
  | 'error'

/** Is the token still usable, per a peeked card? */
export function isUsable(card: ConfirmCardData): boolean {
  return !card.expired && card.consumed_at == null
}

/**
 * Why a peeked card cannot be applied, in words, or `null` when it can.
 *
 * Deliberately mirrors `server/confirm.py:145-147` rather than inventing new
 * phrasing, so the sentence a user sees is the same whether it came from a peek
 * (200 with flags set) or from a rejected apply (409 with `detail`).
 */
export function reasonOf(card: ConfirmCardData): string | null {
  if (card.consumed_at != null) return 'This change was already applied.'
  if (card.expired) return 'This confirmation expired. Ask again to get a fresh one.'
  return null
}

/** The phase a freshly peeked card lands in. */
export function phaseOf(card: ConfirmCardData): ConfirmPhase {
  return isUsable(card) ? 'ready' : 'unusable'
}

/**
 * Which phase a FAILED peek or apply belongs in.
 *
 * The split is the whole point of the distinction between `unusable` and `error`:
 *   - 409 — the token is real but spent or expired (`generate.py:441`). Terminal:
 *     offering a retry would be offering a guaranteed second 409.
 *   - 404 — never issued, or already garbage-collected (`generate.py:429`,
 *     cleanup at `:462`). Also terminal, for the same reason.
 *   - 429 — the `confirm` limit (10/60, `limits.py:129-145`). NOT terminal: the
 *     token is still good and the server told us how long to wait, so this is the
 *     one failure where retrying is the correct advice.
 *   - anything else (5xx, offline) — transient, retry is worth offering.
 */
export function applyFailurePhase(error: unknown): ConfirmPhase {
  if (!isApiError(error)) return 'error'
  if (error.status === 409 || error.status === 404) return 'unusable'
  return 'error'
}

/** Can the user still act on the card in this phase? */
export function canApprove(phase: ConfirmPhase): boolean {
  return phase === 'ready'
}

/**
 * Should a retry button be offered?
 *
 * Only for `error`. `unusable` must not offer one: the fix for a spent token is a
 * fresh preview from the surface that issued it, not another POST.
 */
export function canRetry(phase: ConfirmPhase): boolean {
  return phase === 'error'
}

/** Terminal phases — the card is done and will not change without a new token. */
export function isTerminal(phase: ConfirmPhase): boolean {
  return phase === 'applied' || phase === 'cancelled' || phase === 'unusable'
}

/** The wording for a failed apply. Thin wrapper for the confirm-specific default. */
export function applyFailureMessage(error: unknown): string {
  return messageOf(error, 'Could not apply this change.')
}
