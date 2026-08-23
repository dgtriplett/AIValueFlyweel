// The one confirm card. Preview -> approve -> POST /confirm/{token}.
//
// TIER3_MIGRATION_PLAN.md §4.5: four surfaces (generate, research, proposals,
// chat) share this flow and the console implements it four times. Porting those
// copies would carry four subtly different sets of failure handling into the SPA,
// so this is the primitive they consume instead. Phases 6/7 add the surfaces; this
// phase ships the primitive and its state machine.
//
// The decision logic is NOT here — it is in `lib/confirm.ts`, so it can be tested
// without a renderer (this repo has no DOM test harness). This file is the view:
// it maps a phase to markup and nothing else. The rule is that any question of the
// form "may the user do X now" is answered by a helper, never by an inline boolean
// assembled in JSX, because that is how the console's four copies drifted apart.
//
// WHAT IT DELIBERATELY DOES NOT DO
//  - It does not own the token. The surface that generated the preview does, and
//    passes it in; this card cannot re-issue one.
//  - It does not invalidate react-query keys. What a write invalidates depends on
//    the intent (a research apply re-quantifies the whole portfolio,
//    `AssumptionsView.tsx:22-31`), which this component cannot know. `onApplied`
//    hands the response back and the surface invalidates what it knows changed.
//
// USAGE — the shape phases 6/7 should follow
// ------------------------------------------
// The preview response already carries the card, so pass it as `data` and skip the
// peek. Invalidate in `onApplied`, because only the surface knows what changed:
//
//   const [preview, setPreview] = useState<GenerateResponse | null>(null)
//   const queryClient = useQueryClient()
//
//   {preview?.confirm ? (
//     <ConfirmCard
//       token={preview.confirm.token}
//       data={preview.confirm}
//       approveLabel={`Create ${preview.candidates.length} use cases`}
//       onApplied={() => {
//         queryClient.invalidateQueries({ queryKey: ['use-cases'] })
//         setPreview(null)
//       }}
//       onCancel={() => setPreview(null)}
//     />
//   ) : null}
//
// Restoring a card after a page reload is the other case: pass only `token` and it
// peeks `GET /confirm/{token}`, which is what tells it whether the token survived.
//
//   <ConfirmCard token={tokenFromUrl} onApplied={refetch} />
//
// The state machine is asserted in `frontend/test/plumbing.test.ts`; run it with
// `python3 scripts/check_frontend_tests.py`.

import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, CircleAlert, Clock, Loader2, X } from 'lucide-react'

import { api } from '../api'
import { NO_RETRY, retryPolicy } from '../lib/retry'
import { isLimited, retryHint } from '../lib/errors'
import {
  applyFailureMessage,
  applyFailurePhase,
  canApprove,
  canRetry,
  phaseOf,
  reasonOf,
  type ConfirmPhase,
} from '../lib/confirm'
import type { ConfirmApplyResponse, ConfirmCardData } from '../types'

export interface ConfirmCardProps {
  /** The token from the preview step. Single-use; the server expires it. */
  token?: string
  /**
   * The card contents, when the caller already has them from the preview
   * response. Omit to peek `GET /confirm/{token}` — which is what a card restored
   * after a page reload does, and the reason `readConfirm` exists.
   */
  data?: ConfirmCardData | null
  /** Overrides `data.summary` / the intent when the surface has better words. */
  summary?: string | null
  /** Called with the server's response after a successful apply. */
  onApplied?: (response: ConfirmApplyResponse) => void
  /** Run a caller-owned expensive action after approval instead of consuming a token. */
  onApprove?: () => Promise<unknown>
  /** Called when the user declines. Nothing was written. */
  onCancel?: () => void
  /** Label for the approve button — "Create 4 use cases" beats "Approve". */
  approveLabel?: string
}

export function ConfirmCard({
  token,
  data = null,
  summary,
  onApplied,
  onApprove,
  onCancel,
  approveLabel = 'Approve',
}: ConfirmCardProps) {
  // Peek only when the caller did not already hand us the card. `enabled` keeps
  // this from firing a request against the `confirm` limit for the common case
  // where the preview response already carried everything.
  const peek = useQuery({
    queryKey: ['confirm', token],
    queryFn: () => api.readConfirm(token!),
    enabled: data == null && token != null,
    // A peeked token must not be re-read on a whim: it is cheap but it is rate
    // limited, and its answer only changes when we ourselves consume it.
    staleTime: Infinity,
    retry: retryPolicy,
  })

  const card = data ?? peek.data ?? null

  // `null` until the card is known, so the first paint is `loading` rather than a
  // flash of `ready` for a token that turns out to be spent.
  const [phase, setPhase] = useState<ConfirmPhase>(() =>
    data ? phaseOf(data) : 'loading',
  )
  const [failure, setFailure] = useState<unknown>(null)

  // Adopt the peeked card's phase once it lands. Guarded on the terminal phases so
  // a late-arriving peek cannot walk an already-applied card back to `ready` — the
  // token would be spent and the button would promise a write that 409s.
  useEffect(() => {
    if (!card) return
    setPhase((current) =>
      current === 'loading' || current === 'error' ? phaseOf(card) : current,
    )
  }, [card])

  // Which request failed, so "Try again" retries THAT one. Without this the retry
  // button always applied, meaning a failed PEEK offered a button that consumed the
  // token: the user asked to retry a read and got a write, spending a single-use
  // token they had not approved yet.
  const [failedStep, setFailedStep] = useState<'peek' | 'apply' | null>(null)

  useEffect(() => {
    if (!peek.isError) return
    setFailure(peek.error)
    setFailedStep('peek')
    setPhase(applyFailurePhase(peek.error))
  }, [peek.isError, peek.error])

  const apply = useMutation({
    mutationFn: async () => {
      if (onApprove) {
        const response = await onApprove()
        return {
          ...(response && typeof response === 'object' ? response : {}),
          ok: true,
          intent: card?.intent ?? 'approved_action',
        } as ConfirmApplyResponse
      }
      return api.applyConfirm(token!)
    },
    // Single-use token: an automatic second POST is not a retry, it is a
    // guaranteed 409 that overwrites the real error. See `lib/retry.ts`.
    ...NO_RETRY,
    onMutate: () => {
      setPhase('applying')
      setFailure(null)
      setFailedStep(null)
    },
    onSuccess: (response) => {
      setPhase('applied')
      onApplied?.(response)
    },
    onError: (error) => {
      setFailure(error)
      setFailedStep('apply')
      setPhase(applyFailurePhase(error))
    },
  })

  const cancel = useCallback(() => {
    setPhase('cancelled')
    onCancel?.()
  }, [onCancel])

  const heading = summary ?? card?.summary ?? card?.intent ?? 'Confirm this change'

  return (
    <div
      className="card p-4 space-y-3"
      data-ga-confirm={phase}
      data-ga-confirm-token={token ?? 'local-action'}
    >
      <div className="flex items-start gap-2">
        <PhaseIcon phase={phase} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-navy-100 break-words">{heading}</div>
          {card?.expires_at && phase === 'ready' ? (
            <div className="text-xs text-navy-400 mt-0.5">
              Expires {new Date(card.expires_at).toLocaleTimeString()}
            </div>
          ) : null}
        </div>
      </div>

      {phase === 'loading' ? (
        <div className="text-sm text-navy-400">Checking this confirmation…</div>
      ) : null}

      {/* The reason a peeked card is unusable comes from the card; the reason a
          rejected apply is comes from the server's `detail`. Both end up here. */}
      {phase === 'unusable' ? (
        <div className="text-sm text-warning-400">
          {failure ? applyFailureMessage(failure) : (card ? reasonOf(card) : null) ??
            'This confirmation is no longer usable. Ask again to get a fresh one.'}
        </div>
      ) : null}

      {phase === 'error' ? (
        <div className="text-sm text-lava-400">
          {applyFailureMessage(failure)}
          {/* A rate limit is the one error where we know how long to wait, and
              saying so is the difference between useful advice and "try again". */}
          {isLimited(failure) && retryHint(failure) ? (
            <span className="text-navy-400"> Try again {retryHint(failure)}.</span>
          ) : null}
        </div>
      ) : null}

      {phase === 'applied' ? (
        <div className="text-sm text-success-400">Applied.</div>
      ) : null}

      {phase === 'cancelled' ? (
        <div className="text-sm text-navy-400">Cancelled — nothing was written.</div>
      ) : null}

      {card && (phase === 'ready' || phase === 'applying') ? (
        <ChangePreview card={card} />
      ) : null}

      {/* Buttons only exist where an action exists. `canApprove` / `canRetry` are
          the single source of truth for that, shared with the state machine. */}
      {canApprove(phase) || phase === 'applying' ? (
        <div className="flex gap-2 pt-1">
          <button
            className="btn-primary text-sm"
            disabled={!canApprove(phase)}
            onClick={() => apply.mutate()}
          >
            {phase === 'applying' ? 'Applying…' : approveLabel}
          </button>
          <button className="btn-secondary text-sm" disabled={phase === 'applying'} onClick={cancel}>
            Cancel
          </button>
        </div>
      ) : null}

      {/* Retry the step that failed. A failed peek must re-READ: it never got as
          far as showing the change, so applying now would write something the user
          was never shown. */}
      {canRetry(phase) ? (
        <div className="flex gap-2 pt-1">
          <button
            className="btn-primary text-sm"
            onClick={() => {
              if (failedStep === 'peek') {
                setPhase('loading')
                setFailure(null)
                setFailedStep(null)
                void peek.refetch()
                return
              }
              apply.mutate()
            }}
          >
            Try again
          </button>
          <button className="btn-secondary text-sm" onClick={cancel}>
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  )
}

function PhaseIcon({ phase }: { phase: ConfirmPhase }) {
  if (phase === 'applied') return <Check className="w-4 h-4 mt-0.5 shrink-0 text-success-400" />
  if (phase === 'unusable')
    return <Clock className="w-4 h-4 mt-0.5 shrink-0 text-warning-400" />
  if (phase === 'error')
    return <CircleAlert className="w-4 h-4 mt-0.5 shrink-0 text-lava-400" />
  if (phase === 'cancelled') return <X className="w-4 h-4 mt-0.5 shrink-0 text-navy-500" />
  if (phase === 'loading' || phase === 'applying')
    return <Loader2 className="w-4 h-4 mt-0.5 shrink-0 text-navy-400 animate-spin" />
  return <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-lava-300" />
}

/**
 * `before` / `after` as a diff of scalars.
 *
 * Only keys whose value actually differs are shown: a card listing twenty
 * unchanged fields buries the one that changed, which defeats the purpose of
 * asking for confirmation at all. Non-scalar values are rendered as JSON rather
 * than skipped — an omitted change is worse than an ugly one on a card whose whole
 * job is to say what will happen.
 */
function ChangePreview({ card }: { card: ConfirmCardData }) {
  const before = card.before ?? {}
  const after = card.after ?? {}
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]))
    .filter((key) => render(before[key]) !== render(after[key]))
    .sort()

  if (keys.length === 0) return null

  return (
    <div className="border-t border-navy-700 pt-2 space-y-1">
      {keys.map((key) => (
        <div key={key} className="grid grid-cols-[auto,1fr] gap-x-2 text-xs">
          <span className="text-navy-500 font-mono">{key}</span>
          <span className="text-navy-300 break-words">
            {key in before ? (
              <>
                <span className="line-through text-navy-500">{render(before[key])}</span>{' '}
                <span aria-hidden="true">→</span>{' '}
              </>
            ) : null}
            {render(after[key])}
          </span>
        </div>
      ))}
    </div>
  )
}

/** One value as a string. `undefined` reads as "—", not as "undefined". */
function render(value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value)
}
