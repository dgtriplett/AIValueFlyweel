// Data asset detail drawer: inspect a module/source and see what use cases require it.
//
// Mirrors UseCaseDrawer's overlay pattern — slides from the right, opened via a callback.

import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Database, X } from 'lucide-react'
import { api } from '../api'
import { IngestionBadge, ReadinessBadge } from './Badges'
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
  const {
    data: asset,
    isLoading,
    isError,
  } = useQuery({ queryKey: ['data-asset', assetId], queryFn: () => api.dataAsset(assetId) })

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
              {asset.description ? (
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

            <div className="grid grid-cols-1 gap-3">
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
