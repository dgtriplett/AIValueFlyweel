// Reconcile ingestion status and use-case status against the workspace's system
// tables, then show the diff.
//
// The result modal is not a toast because a sync is not a fire-and-forget action:
// it can silently change nothing (no system-table access, or no linked assets), and
// the only way to tell that apart from "everything is already current" is to read
// the per-table probe and the note lines. Hence the explicit empty-state sentence.

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Cloud, X } from 'lucide-react'

import { api } from '../api'
import type { LiveSyncResponse } from '../types'

type TableProbe = Record<string, boolean>

interface SyncResult {
  diff: LiveSyncResponse
  tables: TableProbe
}

/**
 * `system_tables` is either a per-table map or a single coarse boolean, and only
 * the map can be rendered as a badge row — the boolean form yields no badges.
 */
function probeMap(value: boolean | Record<string, boolean> | undefined): TableProbe | null {
  if (typeof value !== 'object' || value === null) return null
  const entries = Object.entries(value)
  return entries.length ? Object.fromEntries(entries) : null
}

export function DatabricksSyncButton(): JSX.Element {
  const queryClient = useQueryClient()
  const [result, setResult] = useState<SyncResult | null>(null)

  const sync = useMutation({
    mutationFn: () => api.liveSync(true),
    onSuccess: async (diff) => {
      // The badge row is the only thing that explains an empty diff, so the probe
      // comes from /live/status — authoritative about what is reachable right now.
      // A failed status call falls back to whatever the sync itself reported.
      const status = await api.liveStatus().catch(() => null)
      const tables = probeMap(status?.system_tables) ?? probeMap(diff.system_tables) ?? {}
      setResult({ diff, tables })
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  const assetChanges = result?.diff.asset_changes ?? []
  const ucChanges = result?.diff.uc_changes ?? []

  return (
    <>
      <button
        className="btn-secondary text-sm"
        disabled={sync.isPending}
        onClick={() => sync.mutate()}
      >
        <Cloud className="w-4 h-4" /> {sync.isPending ? 'Syncing…' : 'Sync from Databricks'}
      </button>

      {result ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={() => setResult(null)}
        >
          <div className="absolute inset-0 bg-black/60" />
          <div
            className="relative card w-full max-w-lg animate-scale-in"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-bold text-lg flex items-center gap-2">
                <Cloud className="w-5 h-5 text-info" /> Databricks Sync
              </h3>
              <button
                aria-label="Close"
                className="text-navy-400 hover:text-white"
                onClick={() => setResult(null)}
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="text-xs text-navy-400 mb-2">
              System tables:{' '}
              {Object.entries(result.tables).map(([name, reachable]) => (
                <span key={name} className={`badge-${reachable ? 'low' : 'critical'} ml-1`}>
                  {name}
                </span>
              ))}
            </div>

            {(result.diff.notes ?? []).map((note, index) => (
              <div key={index} className="text-xs text-navy-500">
                {note}
              </div>
            ))}

            <div className="mt-3 space-y-1 max-h-64 overflow-y-auto">
              <div className="text-sm font-semibold text-white">
                Data sources advanced to landed ({assetChanges.length})
              </div>
              {assetChanges.slice(0, 20).map((change, index) => (
                <div key={index} className="text-xs text-navy-300">
                  · {change.label}: {change.from} → <span className="text-success">{change.to}</span>
                </div>
              ))}

              <div className="text-sm font-semibold text-white mt-2">
                Use cases reconciled to Live ({ucChanges.length})
              </div>
              {ucChanges.slice(0, 20).map((change, index) => (
                <div key={index} className="text-xs text-navy-300">
                  · {change.asset}: {change.from} → <span className="text-success">live</span>
                </div>
              ))}

              {assetChanges.length === 0 && ucChanges.length === 0 ? (
                <div className="text-xs text-navy-500">
                  No changes — everything already reflects Databricks state (or no linked assets /
                  system-table access).
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
