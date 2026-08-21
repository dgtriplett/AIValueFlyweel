// Create / edit a data source.
//
// Category is a closed list because the whole readiness derivation keys off it,
// but vendor is free text with a `<datalist>` of the ones we know: every customer
// has one system nobody outside the utility has heard of, and a closed vendor
// list would block them at the first field.

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Sparkles, X } from 'lucide-react'

import { api } from '../api'
import {
  INGESTION,
  INGESTION_LABELS,
  SOURCE_CATEGORIES,
  VENDORS_BY_CATEGORY,
} from '../constants'
import type { DataAsset, IngestionStatus, Lob } from '../types'

/** What `POST /agents/decompose-source` puts in its `modules` array. */
interface SuggestedModule {
  module: string
  description?: string | null
}

export function DataAssetModal({
  asset,
  lobs = [],
  onClose,
  onSaved,
}: {
  asset?: DataAsset | null
  lobs?: Lob[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Partial<DataAsset>>({
    source_category: asset?.source_category ?? 'ERP',
    vendor: asset?.vendor ?? '',
    module: asset?.module ?? '',
    description: asset?.description ?? '',
    ingestion_status: asset?.ingestion_status ?? 'not_started',
    owning_lob_id: asset?.owning_lob_id ?? null,
    benefiting_lob_ids: asset?.benefiting_lob_ids ?? [],
  })
  const [decomposing, setDecomposing] = useState(false)
  const [suggestions, setSuggestions] = useState<SuggestedModule[]>([])

  const save = useMutation({
    mutationFn: (body: Partial<DataAsset>) =>
      asset ? api.updateDataAsset(asset.id, body) : api.createDataAsset(body),
    onSuccess: onSaved,
  })

  const toggleBenefiting = (lobId: number) =>
    setForm((current) => {
      const ids = current.benefiting_lob_ids ?? []
      return {
        ...current,
        benefiting_lob_ids: ids.includes(lobId)
          ? ids.filter((id) => id !== lobId)
          : [...ids, lobId],
      }
    })

  const vendors = VENDORS_BY_CATEGORY[form.source_category ?? ''] ?? []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative card w-full max-w-md animate-scale-in">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-bold text-lg">{asset ? 'Edit' : 'New Custom'} Data Source</h3>
          <button aria-label="Close" className="text-navy-400 hover:text-white" onClick={onClose}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-2">
          <div>
            <label htmlFor="asset-source-category" className="text-xs text-navy-500">
              Source category (required)
            </label>
            <select
              id="asset-source-category"
              name="asset-source-category"
              aria-label="Source category"
              className="input-field"
              value={form.source_category ?? ''}
              onChange={(event) => setForm({ ...form, source_category: event.target.value })}
            >
              {SOURCE_CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </div>

          <button
            className="btn-secondary text-xs w-full justify-center"
            disabled={decomposing}
            onClick={async () => {
              setDecomposing(true)
              try {
                const result = await api.decomposeSource(
                  form.source_category ?? '',
                  form.vendor || undefined,
                )
                setSuggestions((result.modules as SuggestedModule[]) ?? [])
              } finally {
                setDecomposing(false)
              }
            }}
          >
            <Sparkles className="w-3 h-3" />{' '}
            {decomposing ? 'Decomposing…' : 'AI: suggest modules for this category'}
          </button>

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion.module}
                  className="text-xs px-2 py-1 rounded-full border bg-navy-700 border-navy-600 text-navy-300 hover:border-lava/50"
                  title={suggestion.description ?? ''}
                  onClick={() =>
                    setForm((current) => ({
                      ...current,
                      module: suggestion.module,
                      description: suggestion.description || current.description,
                    }))
                  }
                >
                  + {suggestion.module}
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="asset-module" className="text-xs text-navy-500">
                Module / subsystem
              </label>
              <input
                id="asset-module"
                name="asset-module"
                aria-label="Module or subsystem"
                className="input-field"
                placeholder="e.g. Plant Maintenance"
                value={form.module ?? ''}
                onChange={(event) => setForm({ ...form, module: event.target.value })}
              />
            </div>
            <div>
              <label htmlFor="asset-vendor" className="text-xs text-navy-500">
                Vendor / product (optional)
              </label>
              <input
                id="asset-vendor"
                name="asset-vendor"
                aria-label="Vendor or product"
                className="input-field"
                list="vendor-opts"
                placeholder="unspecified"
                value={form.vendor ?? ''}
                onChange={(event) => setForm({ ...form, vendor: event.target.value })}
              />
              <datalist id="vendor-opts">
                {vendors.map((vendor) => (
                  <option key={vendor} value={vendor} />
                ))}
              </datalist>
            </div>
          </div>

          <textarea
            id="asset-description"
            name="asset-description"
            aria-label="Data source description"
            className="input-field"
            rows={2}
            placeholder="Description"
            value={form.description ?? ''}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
          />

          <div>
            <label className="text-xs text-navy-500" htmlFor="asset-ingestion-status">
              Ingestion status
            </label>
            <select
              id="asset-ingestion-status"
              name="asset-ingestion-status"
              aria-label="Ingestion status"
              className="input-field"
              value={form.ingestion_status ?? 'not_started'}
              onChange={(event) =>
                setForm({ ...form, ingestion_status: event.target.value as IngestionStatus })
              }
            >
              {INGESTION.map((status) => (
                <option key={status} value={status}>
                  {INGESTION_LABELS[status]}
                </option>
              ))}
            </select>
          </div>

          <select
            id="asset-owning-lob"
            name="asset-owning-lob"
            aria-label="Owning domain (LOB)"
            className="input-field"
            value={form.owning_lob_id ?? ''}
            onChange={(event) =>
              setForm({
                ...form,
                owning_lob_id: event.target.value ? Number(event.target.value) : null,
              })
            }
          >
            <option value="">Owning domain…</option>
            {lobs.map((lob) => (
              <option key={lob.id} value={lob.id}>
                {lob.name}
              </option>
            ))}
          </select>

          <div>
            <div className="text-xs text-navy-500 mb-1">Benefiting domains</div>
            <div className="flex flex-wrap gap-1.5">
              {lobs.map((lob) => {
                const on = (form.benefiting_lob_ids ?? []).includes(lob.id)
                return (
                  <button
                    key={lob.id}
                    className={`text-xs px-2 py-1 rounded-full border ${
                      on
                        ? 'bg-lava/20 border-lava/50 text-lava-300'
                        : 'bg-navy-700 border-navy-600 text-navy-400'
                    }`}
                    onClick={() => toggleBenefiting(lob.id)}
                  >
                    {lob.name}
                  </button>
                )
              })}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-secondary text-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary text-sm"
            disabled={
              !(form.source_category ?? '').trim() || !(form.module ?? '').trim() || save.isPending
            }
            onClick={() => save.mutate(form)}
          >
            {asset ? 'Save' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
