// The data-source registry: every module we could land, grouped by system.
//
// Grouped by `source_category` because that is how a data leader thinks about
// the work ("we're doing the historian this quarter"), and because a flat list of
// several hundred modules is unreadable. Unlike the portfolio, this view owns its
// own query: the shell never needs the asset list, and the inline ingestion
// control writes often enough that a local cache entry is simpler than threading
// invalidations through props.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Database, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'

import { api } from '../api'
import { INGESTION, INGESTION_COLORS, INGESTION_LABELS } from '../constants'
import { DataAssetModal } from '../components/DataAssetModal'
import { DatabricksSyncButton } from '../components/DatabricksSyncButton'
import { SourceRecommendPanel } from '../components/SourceRecommendPanel'
import { matchesDataAsset, useFilters } from '../context/FilterContext'
import type { DataAsset, IngestionStatus, Lob } from '../types'

/** `{}` opens the modal in create mode; `{ asset }` in edit mode. */
interface EditorState {
  asset?: DataAsset
}

export function RegistryView({
  lobs = [],
  onOpenUseCase,
}: {
  lobs?: Lob[]
  onOpenUseCase?: (useCaseId: number) => void
}) {
  const queryClient = useQueryClient()
  const { filters } = useFilters()
  const {
    data: assets = [],
    isLoading,
    isError,
  } = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [editor, setEditor] = useState<EditorState | null>(null)

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteDataAsset(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['data-assets'] }),
  })

  // Was a raw `fetch` that predated the axios wrapper, which meant this write —
  // the one that marks a row user-edited server-side — carried no account header,
  // so it could land against a different tenant than the rows on screen. It is an
  // `api.*` method now, scoped by the shared interceptor like every other call.
  // Landing a module can flip a use case's readiness and redraw the blast radius,
  // so all three caches go.
  const setIngestion = useMutation({
    mutationFn: ({ id, status }: { id: number; status: IngestionStatus }) =>
      api.setIngestionStatus(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      queryClient.invalidateQueries({ queryKey: ['blast'] })
    },
  })

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '—') : '—'

  const visible = assets.filter((asset) => matchesDataAsset(asset, filters))

  const byGroup = new Map<string, DataAsset[]>()
  for (const asset of visible) {
    const key = asset.source_category || asset.source_system || ''
    if (!byGroup.has(key)) byGroup.set(key, [])
    byGroup.get(key)!.push(asset)
  }
  const groups = [...byGroup.entries()].sort((left, right) => left[0].localeCompare(right[0]))

  const governed = visible.filter((asset) => asset.ingestion_status === 'governed').length
  const landed = visible.filter((asset) => asset.ingestion_status !== 'not_started').length

  return (
    <div className="space-y-4">
      <SourceRecommendPanel defaultOpen onOpenUseCase={onOpenUseCase} />

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-navy-400">
          <span className="text-white font-medium">{visible.length}</span> data sources ·{' '}
          <span className="text-white font-medium">{groups.length}</span> systems · {landed} landed+ ·{' '}
          {governed} governed
        </div>
        <div className="flex gap-2">
          <DatabricksSyncButton />
          <button className="btn-primary text-sm" onClick={() => setEditor({})}>
            <Plus className="w-4 h-4" /> Add Custom Source
          </button>
        </div>
      </div>

      <div className="flex items-center gap-4 text-xs text-navy-400">
        <span className="flex items-center gap-1">
          <span className="badge-muted">catalog</span> curated master list
        </span>
        <span className="flex items-center gap-1">
          <span
            className="px-2 py-0.5 rounded-full text-xs font-semibold"
            style={{
              background: 'rgba(255,171,0,0.18)',
              color: '#FFCC66',
              border: '1px solid rgba(255,171,0,0.4)',
            }}
          >
            <Sparkles className="w-3 h-3 inline" /> auto
          </span>{' '}
          inferred from delivered UC
        </span>
        <span className="flex items-center gap-1">
          <span className="badge-electric">custom</span> user-added
        </span>
      </div>

      {isLoading && <div className="text-navy-400">Loading…</div>}
      {isError && (
        <div className="text-lava-300 text-sm">Couldn't load data sources. Please retry.</div>
      )}
      {!isLoading && !isError && groups.length === 0 && (
        <div className="text-navy-500 text-sm">No data sources match the current filters.</div>
      )}

      <div className="space-y-2">
        {groups.map(([group, modules]) => {
          const open = collapsed[group] ?? true
          return (
            <div key={group} className="card p-0 overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-lava/5"
                onClick={() => setCollapsed((current) => ({ ...current, [group]: !open }))}
              >
                <div className="flex items-center gap-2">
                  {open ? (
                    <ChevronDown className="w-4 h-4 text-navy-500" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-navy-500" />
                  )}
                  <Database className="w-4 h-4 text-lava-300" />
                  <span className="font-semibold text-white">{group}</span>
                  <span className="text-xs text-navy-500">{modules.length} modules</span>
                </div>
                <div className="flex gap-1">
                  {INGESTION.map((status) => {
                    const count = modules.filter(
                      (asset) => asset.ingestion_status === status,
                    ).length
                    return count ? (
                      <span
                        key={status}
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: INGESTION_COLORS[status] }}
                        title={`${count} ${INGESTION_LABELS[status]}`}
                      />
                    ) : null
                  })}
                </div>
              </button>

              {open && (
                <div className="border-t border-navy-600">
                  {modules.map((asset) => (
                    <div
                      key={asset.id}
                      className="flex items-center justify-between px-4 py-2.5 border-b border-navy-600/60 last:border-0 hover:bg-navy-700/30 group gap-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-medium truncate text-white flex items-center gap-2">
                          {asset.module}
                          {asset.vendor && (
                            <span
                              className="badge-muted text-[10px]"
                              title="Vendor / product (metadata)"
                            >
                              {asset.vendor}
                            </span>
                          )}
                          {asset.auto_captured && (
                            <span
                              className="px-1.5 py-0.5 rounded-full text-[10px] font-semibold"
                              style={{ background: 'rgba(255,171,0,0.18)', color: '#FFCC66' }}
                              title={asset.auto_note ?? ''}
                            >
                              <Sparkles className="w-2.5 h-2.5 inline" /> auto
                            </span>
                          )}
                          {asset.origin === 'custom' && (
                            <span className="badge-electric text-[10px]">custom</span>
                          )}
                        </div>
                        <div className="text-xs text-navy-500 truncate">{asset.description}</div>
                      </div>

                      <div className="flex items-center rounded border border-navy-600 overflow-hidden shrink-0">
                        {INGESTION.map((status) => (
                          <button
                            key={status}
                            className="text-[11px] px-2 py-1 transition-colors"
                            style={
                              asset.ingestion_status === status
                                ? {
                                    background: `${INGESTION_COLORS[status]}33`,
                                    color: INGESTION_COLORS[status],
                                    fontWeight: 600,
                                  }
                                : { color: '#618794' }
                            }
                            onClick={() => setIngestion.mutate({ id: asset.id, status })}
                          >
                            {INGESTION_LABELS[status]}
                          </button>
                        ))}
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <span
                          className="text-xs text-navy-400 w-24 truncate text-right"
                          title="Owning domain"
                        >
                          {lobName(asset.owning_lob_id)}
                        </span>
                        {(asset.benefiting_lob_ids ?? []).length > 0 && (
                          <span
                            className="text-xs text-info"
                            title={`Benefiting: ${(asset.benefiting_lob_ids ?? [])
                              .map(lobName)
                              .join(', ')}`}
                          >
                            +{(asset.benefiting_lob_ids ?? []).length}
                          </span>
                        )}
                        <button
                          aria-label={`Edit ${asset.source_system} ${asset.module}`}
                          className="text-navy-600 hover:text-lava-300 opacity-0 group-hover:opacity-100"
                          onClick={() => setEditor({ asset })}
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          aria-label={`Delete ${asset.source_system} ${asset.module}`}
                          className="text-navy-600 hover:text-lava opacity-0 group-hover:opacity-100"
                          onClick={() => {
                            if (confirm(`Delete "${asset.source_system} / ${asset.module}"?`)) {
                              remove.mutate(asset.id)
                            }
                          }}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {editor && (
        <DataAssetModal
          asset={editor.asset}
          lobs={lobs}
          onClose={() => setEditor(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ['data-assets'] })
            setEditor(null)
          }}
        />
      )}
    </div>
  )
}
