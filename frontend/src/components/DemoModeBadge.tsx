// The header control for the showcase dataset — only present when the server says
// demo mode is on.
//
// These three calls are raw `fetch`, not the axios client, deliberately: the demo
// routes are a deployment affordance rather than part of the product API surface,
// and keeping them out of `api` means the endpoint list in api.ts stays the set of
// routes the app actually depends on to function.

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Database, LoaderCircle, RotateCcw, Sparkles } from 'lucide-react'

import type { DemoStatus } from '../types'

type Action = 'load' | 'reset'

export function DemoModeBadge() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<DemoStatus | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)
  const [error, setError] = useState<string | null>(null)

  // The `alive` guard keeps a slow status response from setting state after the
  // header has been torn down.
  useEffect(() => {
    let alive = true
    fetch('/api/demo/status')
      .then((response) => (response.ok ? response.json() : null))
      .then((data: DemoStatus | null) => {
        if (alive) setStatus(data)
      })
      .catch(() => {
        if (alive) setStatus(null)
      })
    return () => {
      alive = false
    }
  }, [])

  if (!status?.enabled) return null

  const run = async (action: Action) => {
    setBusy(action)
    setError(null)
    try {
      const response = await fetch(`/api/demo/${action}`, { method: 'POST' })
      const body: { detail?: string; mode?: string } = await response
        .json()
        .catch(() => ({}))
      if (!response.ok) throw new Error(body?.detail || `HTTP ${response.status}`)
      setStatus((current) => ({ ...(current ?? { enabled: true }), mode: body.mode }))
      // Load/reset rewrites the whole database, so nothing cached survives it.
      await queryClient.invalidateQueries()
    } catch (caught) {
      setError((caught as Error)?.message || 'failed')
    } finally {
      setBusy(null)
    }
  }

  const mode =
    status.mode === 'demo' ? 'showcase loaded' : status.mode === 'clean' ? 'clean (day-1)' : '—'

  return (
    <div className="flex items-center gap-2 rounded-card border border-warning-600/40 bg-warning-600/10 px-2 py-1">
      <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-warning-400">
        <Sparkles className="w-3 h-3" /> Demo Mode
      </span>
      <span className="text-[11px] text-navy-300">
        {error ? (
          <span className="text-lava-400" title={error}>
            error
          </span>
        ) : (
          mode
        )}
      </span>
      <button
        onClick={() => run('load')}
        disabled={busy !== null}
        title="Populate the showcase dataset"
        className="flex items-center gap-1 rounded-btn bg-warning-600 px-2 py-1 text-[11px] font-semibold text-navy-900 hover:bg-warning-500 disabled:opacity-50"
      >
        {busy === 'load' ? (
          <LoaderCircle className="w-3 h-3 animate-spin" />
        ) : (
          <Database className="w-3 h-3" />
        )}
        Load demo data
      </button>
      <button
        onClick={() => run('reset')}
        disabled={busy !== null}
        title="Reset to the pristine day-1 state"
        className="flex items-center gap-1 rounded-btn border border-navy-500 px-2 py-1 text-[11px] font-semibold text-navy-300 hover:bg-navy-700 disabled:opacity-50"
      >
        {busy === 'reset' ? (
          <LoaderCircle className="w-3 h-3 animate-spin" />
        ) : (
          <RotateCcw className="w-3 h-3" />
        )}
        Reset to clean
      </button>
    </div>
  )
}
