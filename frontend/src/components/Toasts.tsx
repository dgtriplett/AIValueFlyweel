// Transient notifications — the one place a failure that isn't tied to a form
// gets said out loud.
//
// Deliberately NOT wired into the axios interceptor. A toast is a UI decision
// ("was this failure worth interrupting for?") and the interceptor cannot know:
// a background refetch failing while a stale table is still on screen should stay
// quiet, while a rate-limited click the user is waiting on should not. So the
// interceptor normalizes errors (`lib/errors.ts`) and a call site decides whether
// to show one. `useApiErrorToast` makes that decision one line long.
//
// Kept minimal on purpose: an array of messages, auto-dismiss, no queueing
// policy, no portal. Anything more is a library, and this needs to be a
// primitive that later phases can rely on rather than fight.

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { CircleAlert, Info, TriangleAlert, X } from 'lucide-react'

import { isApiError, retryHint } from '../lib/errors'

export type ToastKind = 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
  /** Shown smaller under the message — the "wait 12s" half of a rate limit. */
  hint?: string | null
}

/** How long each kind stays up. Errors linger; info gets out of the way. */
const DISMISS_MS: Record<ToastKind, number> = {
  error: 8000,
  warning: 6000,
  info: 4000,
}

interface ToastApi {
  show: (toast: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  // Ids come from a ref, not the array length: two toasts dismissed out of order
  // would otherwise collide on a key and React would reuse the wrong node.
  const nextId = useRef(1)
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const show = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = nextId.current++
      setToasts((current) => {
        // Identical back-to-back messages are collapsed rather than stacked:
        // three failed retries of one action is one problem, not three.
        const duplicate = current.find(
          (existing) => existing.message === toast.message && existing.kind === toast.kind,
        )
        if (duplicate) return current
        // A hard cap, so a retry storm cannot bury the screen in cards.
        return [...current, { ...toast, id }].slice(-4)
      })
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), DISMISS_MS[toast.kind]),
      )
    },
    [dismiss],
  )

  const value = useMemo<ToastApi>(() => ({ show, dismiss }), [show, dismiss])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

const STYLES: Record<ToastKind, { border: string; icon: typeof CircleAlert; tint: string }> = {
  error: { border: 'border-l-lava', icon: CircleAlert, tint: 'text-lava-400' },
  warning: { border: 'border-l-warning', icon: TriangleAlert, tint: 'text-warning-400' },
  info: { border: 'border-l-navy-400', icon: Info, tint: 'text-navy-300' },
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[]
  onDismiss: (id: number) => void
}) {
  if (toasts.length === 0) return null
  return (
    // Bottom-LEFT: the Genie FAB owns bottom-right, and a toast that covers the
    // control you just clicked hides the thing you are trying to retry.
    // `aria-live="polite"` so a screen reader announces it without cutting off
    // whatever it was already reading.
    <div
      className="fixed bottom-5 left-5 z-50 flex flex-col gap-2 w-[340px] max-w-[92vw]"
      aria-live="polite"
      role="status"
    >
      {toasts.map((toast) => {
        const style = STYLES[toast.kind]
        const Icon = style.icon
        return (
          <div
            key={toast.id}
            data-ga-toast={toast.kind}
            className={`card p-3 border-l-4 ${style.border} flex items-start gap-2 animate-scale-in`}
          >
            <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${style.tint}`} />
            <div className="min-w-0 flex-1">
              <div className="text-sm text-navy-200 break-words">{toast.message}</div>
              {toast.hint ? (
                <div className="text-xs text-navy-400 mt-0.5">{toast.hint}</div>
              ) : null}
            </div>
            <button
              aria-label="Dismiss notification"
              className="text-navy-500 hover:text-white shrink-0"
              onClick={() => onDismiss(toast.id)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Show a toast. Throws outside a `ToastProvider` rather than no-opping, because a
 * silently swallowed error notification is the bug this primitive exists to fix.
 */
export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside a <ToastProvider>')
  return context
}

/**
 * Report a caught error as a toast, using the server's own words.
 *
 * The `fallback` is only used when the error carried no message — a network
 * failure with no response, say. A rate limit gets the "wait 12s" clause as the
 * hint, which is the whole point: `server/limits.py:191` writes these messages to
 * be actionable, and the SPA used to throw them away.
 */
export function useApiErrorToast() {
  const { show } = useToast()
  return useCallback(
    (error: unknown, fallback = 'Something went wrong.') => {
      if (isApiError(error)) {
        show({
          // A limit is a "wait", not a fault, so it reads as a warning.
          kind: error.isRateLimited ? 'warning' : 'error',
          message: error.message || fallback,
          hint: error.isRateLimited ? (retryHint(error) ?? null) : null,
        })
        return
      }
      const message = error instanceof Error && error.message ? error.message : fallback
      show({ kind: 'error', message })
    },
    [show],
  )
}
