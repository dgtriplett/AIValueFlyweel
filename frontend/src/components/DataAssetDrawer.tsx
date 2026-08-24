// Data asset detail drawer: inspect a module/source and see what use cases require it.
//
// Mirrors UseCaseDrawer's overlay pattern — slides from the right, opened via a callback.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Database, X, Pencil, Check, XCircle } from 'lucide-react'
import { useState, useEffect } from 'react'
import { api } from '../api'
import { IngestionBadge, ReadinessBadge } from './Badges'
import { useToast } from './Toasts'
import type { Lob } from '../types'

export function DataAssetDrawer({
  assetId,
  lobs,
  onClose,
  onOpenUseCase,
}: {
  assetId: number
  lobs: Lob[]
  onClose: () => void
  onOpenUseCase: (id: number) => void
}) {
  const queryClient = useQueryClient()
  const { show } = useToast()
  const [isEditing, setIsEditing] = useState(false)
  const [editForm, setEditForm] = useState({
    description: '',
    provides: '',
    steward: '',
    source_of_record: '',
    refresh_cadence: '',
  })

  const {
    data: asset,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['data-asset', assetId],
    queryFn: () => api.dataAsset(assetId),
  })

  // Initialize form when data loads
  useEffect(() => {
    if (asset) {
      setEditForm({
        description: asset.description || '',
        provides: asset.provides || '',
        steward: asset.steward || '',
        source_of_record: asset.source_of_record || '',
        refresh_cadence: asset.refresh_cadence || '',
      })
    }
  }, [asset])

  const updateMutation = useMutation({
    mutationFn: (body: Partial<typeof editForm>) =>
      api.updateDataAsset(assetId, {
        ...asset,
        ...body,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['data-asset', assetId] })
      setIsEditing(false)
      show({ kind: 'info', message: 'Asset details updated' })
    },
    onError: (error: Error) => {
      show({ kind: 'error', message: `Failed to update asset: ${error.message}` })
    },
  })

  const handleEdit = () => {
    if (!asset) return
    setEditForm({
      description: asset.description || '',
      provides: asset.provides || '',
      steward: asset.steward || '',
      source_of_record: asset.source_of_record || '',
      refresh_cadence: asset.refresh_cadence || '',
    })
    setIsEditing(true)
  }

  const handleSave = () => {
    updateMutation.mutate(editForm)
  }

  const handleCancel = () => {
    setIsEditing(false)
    if (asset) {
      setEditForm({
        description: asset.description || '',
        provides: asset.provides || '',
        steward: asset.steward || '',
        source_of_record: asset.source_of_record || '',
        refresh_cadence: asset.refresh_cadence || '',
      })
    }
  }

  const lobName = (id?: number | null) =>
    id != null ? (lobs.find((lob) => lob.id === id)?.name ?? '—') : '—'

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-navy-800 border-l border-navy-600 overflow-y-auto animate-in slide-in-from-right shadow-card-hover">
        <div className="sticky top-0 bg-navy-800/95 backdrop-blur border-b border-navy-600 px-5 py-3 flex items-center justify-between z-10">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-lava-300" />
            <span className="text-xs text-navy-500">Data Asset #{assetId}</span>
            {asset ? <IngestionBadge status={asset.ingestion_status} /> : null}
          </div>
          <button
            aria-label="Close"
            className="text-navy-400 hover:text-white"
            onClick={onClose}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {isError ? (
          <div className="p-6 text-lava-300 text-sm">
            Couldn't load this data asset. Please close and try again.
          </div>
        ) : isLoading || !asset ? (
          <div className="p-6 text-navy-400">Loading…</div>
        ) : (
          <div className="p-5 space-y-5">
            <div>
              <h2 className="text-xl font-bold text-white">
                <span className="text-lava-300">{asset.source_system}</span> · {asset.module}
              </h2>
              {asset.vendor ? (
                <p className="text-navy-400 text-sm mt-1">Vendor: {asset.vendor}</p>
              ) : null}
              {!isEditing && asset.description ? (
                <p className="text-navy-300 mt-2 text-sm">{asset.description}</p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2 mt-3 text-xs">
                <span className="badge-muted">{lobName(asset.owning_lob_id)}</span>
                {asset.sub_vertical ? (
                  <span className="badge-muted">{asset.sub_vertical}</span>
                ) : null}
                {asset.origin === 'custom' ? (
                  <span className="badge-muted" title="Customer-authored">
                    Custom
                  </span>
                ) : null}
              </div>
            </div>

            <div className="relative">
              {/* Edit button in top-right corner */}
              {!isEditing && (
                <button
                  onClick={handleEdit}
                  className="absolute -top-2 right-0 p-2 text-navy-400 hover:text-lava-300 hover:bg-navy-700/50 rounded transition-colors"
                  title="Edit asset details"
                  aria-label="Edit asset details"
                >
                  <Pencil className="w-4 h-4" />
                </button>
              )}

              <div className="grid grid-cols-1 gap-3 pt-2">
                {isEditing ? (
                  <>
                    <div className="card p-3">
                      <label className="text-xs text-navy-500 mb-1 block">Description</label>
                      <textarea
                        value={editForm.description}
                        onChange={(e) =>
                          setEditForm({ ...editForm, description: e.target.value })
                        }
                        className="w-full bg-navy-700 border border-navy-600 rounded px-2 py-1.5 text-sm text-navy-100 focus:outline-none focus:border-lava-400"
                        rows={2}
                      />
                    </div>

                    <div className="card p-3">
                      <label className="text-xs text-navy-500 mb-1 block">Provides</label>
                      <textarea
                        value={editForm.provides}
                        onChange={(e) => setEditForm({ ...editForm, provides: e.target.value })}
                        className="w-full bg-navy-700 border border-navy-600 rounded px-2 py-1.5 text-sm text-navy-100 focus:outline-none focus:border-lava-400"
                        rows={3}
                        placeholder="What business capabilities or data this module provides..."
                      />
                    </div>

                    <div className="card p-3">
                      <label className="text-xs text-navy-500 mb-1 block">Refresh cadence</label>
                      <input
                        type="text"
                        value={editForm.refresh_cadence}
                        onChange={(e) =>
                          setEditForm({ ...editForm, refresh_cadence: e.target.value })
                        }
                        className="w-full bg-navy-700 border border-navy-600 rounded px-2 py-1.5 text-sm text-navy-100 focus:outline-none focus:border-lava-400"
                        placeholder="e.g., Daily batch, Real-time, Hourly"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="card p-3">
                        <label className="text-xs text-navy-500 mb-1 block">Steward</label>
                        <input
                          type="text"
                          value={editForm.steward}
                          onChange={(e) =>
                            setEditForm({ ...editForm, steward: e.target.value })
                          }
                          className="w-full bg-navy-700 border border-navy-600 rounded px-2 py-1.5 text-sm text-navy-100 focus:outline-none focus:border-lava-400"
                          placeholder="Team or person"
                        />
                      </div>

                      <div className="card p-3">
                        <label className="text-xs text-navy-500 mb-1 block">
                          Source of record
                        </label>
                        <input
                          type="text"
                          value={editForm.source_of_record}
                          onChange={(e) =>
                            setEditForm({ ...editForm, source_of_record: e.target.value })
                          }
                          className="w-full bg-navy-700 border border-navy-600 rounded px-2 py-1.5 text-sm text-navy-100 focus:outline-none focus:border-lava-400"
                          placeholder="Canonical system"
                        />
                      </div>
                    </div>

                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={handleSave}
                        disabled={updateMutation.isPending}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-lava-500 hover:bg-lava-600 disabled:bg-navy-600 text-white text-sm rounded transition-colors"
                      >
                        <Check className="w-4 h-4" />
                        {updateMutation.isPending ? 'Saving…' : 'Save'}
                      </button>
                      <button
                        onClick={handleCancel}
                        disabled={updateMutation.isPending}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-navy-700 hover:bg-navy-600 disabled:bg-navy-600 text-navy-300 text-sm rounded transition-colors"
                      >
                        <XCircle className="w-4 h-4" />
                        Cancel
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="card p-3">
                      <div className="text-xs text-navy-500 mb-1">Provides</div>
                      <div className="text-sm text-navy-300">
                        {asset.provides || 'Not documented yet'}
                      </div>
                    </div>

                    <div className="card p-3">
                      <div className="text-xs text-navy-500 mb-1">Refresh cadence</div>
                      <div className="text-sm text-navy-300">
                        {asset.refresh_cadence || 'Not documented yet'}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="card p-3">
                        <div className="text-xs text-navy-500 mb-1">Steward</div>
                        <div className="text-sm text-navy-300">
                          {asset.steward || 'Not documented yet'}
                        </div>
                      </div>

                      <div className="card p-3">
                        <div className="text-xs text-navy-500 mb-1">Source of record</div>
                        <div className="text-sm text-navy-300">
                          {asset.source_of_record || 'Not documented yet'}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            <section>
              <h3 className="text-sm font-semibold text-navy-300 mb-2 flex items-center gap-1.5">
                <ArrowRight className="w-4 h-4 text-lava-300" /> Required by use cases (
                {(asset.required_by ?? []).length})
              </h3>
              <div className="space-y-1">
                {(asset.required_by ?? []).map((req) => (
                  <button
                    key={req.use_case_id}
                    className="w-full text-left flex items-center justify-between text-sm border-b border-navy-600 py-1.5 hover:bg-navy-700/50 px-1 rounded"
                    onClick={() => onOpenUseCase(req.use_case_id)}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-navy-300 truncate">{req.title}</span>
                        {req.criticality === 'helpful' ? (
                          <span className="text-navy-500 text-xs">(helpful)</span>
                        ) : null}
                      </div>
                      {req.rationale ? (
                        <div className="text-xs text-navy-500 mt-0.5">{req.rationale}</div>
                      ) : null}
                    </div>
                    <div className="shrink-0 ml-2">
                      {req.readiness ? (
                        <ReadinessBadge readiness={req.readiness} />
                      ) : null}
                    </div>
                  </button>
                ))}
                {(asset.required_by ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">
                    No use cases currently require this asset.
                  </div>
                ) : null}
              </div>
            </section>

            {asset.uc_catalog || asset.uc_schema ? (
              <section className="card p-3">
                <div className="text-xs text-navy-500 mb-1">Unity Catalog location</div>
                <div className="text-xs text-navy-300 font-mono">
                  {asset.uc_catalog && asset.uc_schema
                    ? `${asset.uc_catalog}.${asset.uc_schema}`
                    : asset.uc_catalog || asset.uc_schema || '—'}
                </div>
              </section>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
