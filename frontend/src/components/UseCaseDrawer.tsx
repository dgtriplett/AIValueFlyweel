// The use-case detail drawer: read, edit, advance, quantify, and link.
//
// It slides over the current view rather than navigating away because you nearly
// always open one FROM a table or the flywheel and want to go straight back.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  Bot,
  Database,
  MessageSquare,
  Pencil,
  Save,
  TrendingUp,
  X,
} from 'lucide-react'
import { api } from '../api'
import {
  STATUSES,
  STATUS_LABELS,
  SUB_VERTICALS,
  SUB_VERTICAL_LABELS,
  READINESS_LABELS,
  fmtMoney,
  subVerticalLabel,
} from '../constants'
import { IngestionBadge, ReadinessBadge, StatusBadge } from './Badges'
import type { Lob, Status, UseCase, ValueComponent } from '../types'

/** The subset of the row the edit form owns; the rest of the record is read-only. */
type Draft = Pick<
  UseCase,
  | 'title'
  | 'description'
  | 'lob_id'
  | 'sub_vertical'
  | 'phase'
  | 'status'
  | 'category'
  | 'effort_tshirt'
  | 'priority_score'
  | 'risk_tags'
  | 'compliance_tags'
>

function multiplierOf(component: ValueComponent): number {
  return typeof component.multiplier === 'number' ? component.multiplier : 1
}

export function UseCaseDrawer({
  ucId,
  lobs,
  onClose,
  onOpenUseCase,
}: {
  ucId: number
  lobs: Lob[]
  onClose: () => void
  onOpenUseCase: (id: number) => void
}) {
  const queryClient = useQueryClient()
  const {
    data: detail,
    isLoading,
    isError,
  } = useQuery({ queryKey: ['uc-detail', ucId], queryFn: () => api.useCaseDetail(ucId) })
  const assumptionsQuery = useQuery({ queryKey: ['assumptions'], queryFn: api.assumptions })
  const assetsQuery = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })

  const [editing, setEditing] = useState(false)
  const [addModuleId, setAddModuleId] = useState<number | ''>('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [comment, setComment] = useState('')
  const [realizedMode, setRealizedMode] = useState<'calculated' | 'override'>('calculated')
  const [actuals, setActuals] = useState<Record<number, number>>({})
  const [overrideAmount, setOverrideAmount] = useState<number | ''>('')
  const [overrideNote, setOverrideNote] = useState('')

  // Reset the form from the server record whenever it changes — but not while the
  // user is editing, or a background refetch would discard what they have typed.
  useEffect(() => {
    if (!detail || editing) return
    setDraft({
      title: detail.title,
      description: detail.description ?? '',
      lob_id: detail.lob_id,
      sub_vertical: detail.sub_vertical,
      phase: detail.phase,
      status: detail.status,
      category: detail.category,
      effort_tshirt: detail.effort_tshirt,
      priority_score: detail.priority_score,
      risk_tags: detail.risk_tags,
      compliance_tags: detail.compliance_tags,
    })
    setRealizedMode(detail.realized_override_enabled ? 'override' : 'calculated')
    setOverrideAmount(detail.realized_override_amount ?? '')
    setOverrideNote(detail.realized_override_note ?? '')
    const components =
      detail.realized_value_json?.components ?? detail.hypothesized_value_json?.components ?? []
    const next: Record<number, number> = {}
    components.forEach((component, index) => {
      next[index] = multiplierOf(component)
    })
    setActuals(next)
  }, [detail, editing])

  const invalidateDetail = () => {
    queryClient.invalidateQueries({ queryKey: ['uc-detail', ucId] })
    queryClient.invalidateQueries({ queryKey: ['use-cases'] })
  }

  const save = useMutation({
    mutationFn: (body: Draft) => api.updateUseCase(ucId, body),
    onSuccess: () => {
      invalidateDetail()
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      queryClient.invalidateQueries({ queryKey: ['blast'] })
      setEditing(false)
    },
  })

  const advance = useMutation({
    mutationFn: () => api.advanceStatus(ucId),
    onSuccess: () => {
      invalidateDetail()
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      queryClient.invalidateQueries({ queryKey: ['blast'] })
    },
  })

  const setPortfolio = useMutation({
    mutationFn: (inPortfolio: boolean) => api.setInPortfolio(ucId, inPortfolio),
    onSuccess: () => {
      invalidateDetail()
      queryClient.invalidateQueries({ queryKey: ['recommend-catalog'] })
      queryClient.invalidateQueries({ queryKey: ['analytics-dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
    },
  })

  const invalidateRequires = () => {
    invalidateDetail()
    queryClient.invalidateQueries({ queryKey: ['requires'] })
    queryClient.invalidateQueries({ queryKey: ['blast'] })
  }

  const addRequired = useMutation({
    // manual: true — a hand-added module locks the mapping so the automatic
    // remapper cannot overwrite a deliberate choice.
    mutationFn: (assetId: number) =>
      api.createRequires({
        use_case_id: ucId,
        data_asset_id: assetId,
        criticality: 'required',
        manual: true,
      }),
    onSuccess: () => {
      setAddModuleId('')
      invalidateRequires()
    },
  })

  const removeRequired = useMutation({
    mutationFn: (assetId: number) => api.deleteRequires(ucId, assetId, true),
    onSuccess: invalidateRequires,
  })

  const saveRealized = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error('no use case loaded')
      const components = detail.hypothesized_value_json?.components ?? []
      return api.updateUseCase(ucId, {
        title: detail.title,
        realized_value_json: {
          driver: 'Realized (actuals)',
          components: components.map((component, index) => ({
            name: component.name,
            calculationDisplay: component.calculationDisplay,
            multiplier: actuals[index] ?? multiplierOf(component),
            assumptionKeys: component.assumptionKeys,
            hypMultiplier: multiplierOf(component),
          })),
        },
        realized_override_enabled: realizedMode === 'override',
        realized_override_amount: overrideAmount === '' ? null : Number(overrideAmount),
        realized_override_note: overrideNote || null,
      })
    },
    onSuccess: () => {
      invalidateDetail()
      queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
      queryClient.invalidateQueries({ queryKey: ['blast'] })
    },
  })

  const postComment = useMutation({
    mutationFn: (body: string) =>
      api.createComment({ entity_type: 'use_case', entity_id: ucId, body }),
    onSuccess: () => {
      setComment('')
      queryClient.invalidateQueries({ queryKey: ['uc-detail', ucId] })
    },
  })

  const lobName = (id?: number | null) =>
    id != null ? (lobs.find((lob) => lob.id === id)?.name ?? '—') : '—'

  const assumptionValues: Record<string, number> = {}
  ;(assumptionsQuery.data ?? []).forEach((assumption) => {
    assumptionValues[assumption.key] = assumption.value
  })

  const components = detail?.hypothesized_value_json?.components ?? []

  // Realized value is recomputed from the SHARED assumptions rather than stored,
  // so editing a global assumption re-quantifies every use case at once.
  const calculatedRealized = components.reduce((total, component, index) => {
    let value = actuals[index] ?? multiplierOf(component)
    for (const key of component.assumptionKeys ?? []) value *= assumptionValues[key] ?? 0
    return total + value
  }, 0)

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-navy-800 border-l border-navy-600 overflow-y-auto animate-in slide-in-from-right shadow-card-hover">
        <div className="sticky top-0 bg-navy-800/95 backdrop-blur border-b border-navy-600 px-5 py-3 flex items-center justify-between z-10">
          <div className="flex items-center gap-2">
            <span className="text-xs text-navy-500">Use Case #{ucId}</span>
            {detail ? (
              <ReadinessBadge
                readiness={detail.readiness}
                pendingPrereqs={detail.pending_prereqs}
              />
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {editing ? (
              <button
                className="text-success hover:text-white flex items-center gap-1 text-sm disabled:opacity-50"
                disabled={save.isPending || !draft?.title}
                onClick={() => draft && save.mutate(draft)}
              >
                <Save className="w-4 h-4" /> Save
              </button>
            ) : (
              <button
                className="text-navy-400 hover:text-lava-300 flex items-center gap-1 text-sm"
                onClick={() => setEditing(true)}
              >
                <Pencil className="w-4 h-4" /> Edit
              </button>
            )}
            {/* You decide to write a proposal while looking AT a use case, so the
                action belongs here. Guarded on the id: the drawer renders before
                its data arrives, and a link built then would read
                #proposals/undefined and open the console pointed at nothing. */}
            {ucId ? (
              <a
                data-gaProposalBtn="1"
                href={`/console/#proposals/${ucId}`}
                title="Generate an eight-section proposal for this use case, grounded in its computed value, its real data gaps and this instance’s company profile"
                className="text-navy-400 hover:text-lava-300 flex items-center gap-1 text-sm"
              >
                ✎ Write proposal
              </a>
            ) : null}
            <button
              aria-label="Close"
              className="text-navy-400 hover:text-white"
              onClick={onClose}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {isError ? (
          <div className="p-6 text-lava-300 text-sm">
            Couldn't load this use case. Please close and try again.
          </div>
        ) : isLoading || !detail || !draft ? (
          <div className="p-6 text-navy-400">Loading…</div>
        ) : (
          <div className="p-5 space-y-5">
            {editing ? (
              <div className="space-y-2">
                <input
                  id="uc-title"
                  name="uc-title"
                  aria-label="Use case title"
                  className="input-field"
                  value={draft.title ?? ''}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                />
                <textarea
                  id="uc-description"
                  name="uc-description"
                  aria-label="Use case description"
                  className="input-field"
                  rows={3}
                  value={draft.description ?? ''}
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                />
                <div className="grid grid-cols-2 gap-2">
                  <select
                    id="uc-lob"
                    name="uc-lob"
                    aria-label="Domain (LOB)"
                    className="input-field"
                    value={draft.lob_id ?? ''}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        lob_id: event.target.value ? Number(event.target.value) : null,
                      })
                    }
                  >
                    <option value="">Domain…</option>
                    {lobs.map((lob) => (
                      <option key={lob.id} value={lob.id}>
                        {lob.name}
                      </option>
                    ))}
                  </select>
                  <select
                    id="uc-subvertical"
                    name="uc-subvertical"
                    aria-label="Generation focus"
                    title="Generation focus"
                    className="input-field"
                    value={draft.sub_vertical ?? ''}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        sub_vertical: (event.target.value || null) as Draft['sub_vertical'],
                      })
                    }
                  >
                    <option value="">Generation focus…</option>
                    {SUB_VERTICALS.map((value) => (
                      <option key={value} value={value}>
                        {SUB_VERTICAL_LABELS[value]}
                      </option>
                    ))}
                  </select>
                  <select
                    id="uc-status"
                    name="uc-status"
                    aria-label="Project status"
                    className="input-field"
                    value={draft.status ?? ''}
                    onChange={(event) =>
                      setDraft({ ...draft, status: event.target.value as Status })
                    }
                  >
                    {STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {STATUS_LABELS[value]}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="text-xs text-navy-500">
                  Setting status to Live/Value realized auto-lands this use case's required
                  data assets. Dependency order updates automatically as relationships change.
                </p>
              </div>
            ) : (
              <div>
                <h2 className="text-xl font-bold text-white">{detail.title}</h2>
                <p className="text-navy-300 mt-1 text-sm">{detail.description}</p>
                <div className="flex flex-wrap items-center gap-2 mt-3 text-xs">
                  <span className="badge-muted">{lobName(detail.lob_id)}</span>
                  {detail.sub_vertical ? (
                    <span className="badge-muted" title="Generation focus">
                      {subVerticalLabel(detail.sub_vertical)}
                    </span>
                  ) : null}
                  {detail.origin === 'custom' ? (
                    <span className="badge-muted" title="Customer-authored">
                      Custom
                    </span>
                  ) : null}
                  {detail.in_portfolio ? (
                    <>
                      <StatusBadge status={detail.status} />
                      {detail.effort_tshirt ? (
                        <span className="badge-muted">Effort {detail.effort_tshirt}</span>
                      ) : null}
                      {(() => {
                        const index = detail.status ? STATUSES.indexOf(detail.status) : -1
                        const next =
                          index >= 0 && index < STATUSES.length - 1 ? STATUSES[index + 1] : null
                        return next ? (
                          <button
                            className="badge-muted inline-flex items-center gap-1 hover:text-white hover:border-info disabled:opacity-50"
                            disabled={advance.isPending}
                            title={`Advance to ${STATUS_LABELS[next]}`}
                            onClick={() => advance.mutate()}
                          >
                            Advance <ArrowRight className="w-3 h-3" /> {STATUS_LABELS[next]}
                          </button>
                        ) : (
                          <span className="badge-muted text-navy-500" title="Final stage">
                            Fully realized
                          </span>
                        )
                      })()}
                      <button
                        className="badge-muted inline-flex items-center gap-1 text-success hover:text-lava-300 disabled:opacity-50"
                        disabled={setPortfolio.isPending}
                        title="In your portfolio — click to remove"
                        onClick={() => setPortfolio.mutate(false)}
                      >
                        In portfolio
                      </button>
                    </>
                  ) : (
                    <button
                      className="badge-muted inline-flex items-center gap-1 hover:text-white hover:border-info disabled:opacity-50"
                      disabled={setPortfolio.isPending}
                      title="Catalog idea — add it to your portfolio"
                      onClick={() => setPortfolio.mutate(true)}
                    >
                      + Add to portfolio
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="card p-3 flex items-center justify-between">
              <div className="text-sm">
                <span className="text-navy-400">Readiness: </span>
                <span className="font-semibold text-white">
                  {detail.readiness ? READINESS_LABELS[detail.readiness] : '—'}
                </span>
              </div>
              <div className="text-xs text-navy-400">
                {detail.required_ready}/{detail.required_total} required assets ready
              </div>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {(detail.compliance_tags ?? []).map((tag) => (
                <span key={tag} className="badge-high">
                  {tag}
                </span>
              ))}
              {(detail.risk_tags ?? []).map((tag) => (
                <span key={tag} className="badge-muted">
                  {tag}
                </span>
              ))}
            </div>

            <section>
              <h3 className="text-sm font-semibold text-navy-300 mb-2 flex items-center gap-1.5">
                <TrendingUp className="w-4 h-4 text-lava-300" /> Hypothesized value
              </h3>
              {detail.value_range ? (
                <div className="text-sm text-navy-300">
                  <span className="text-lava-300 font-bold text-lg">
                    {fmtMoney(detail.value_range.mid)}
                  </span>
                  <span className="text-navy-500">
                    {' '}
                    / yr · range {fmtMoney(detail.value_range.low)}–
                    {fmtMoney(detail.value_range.high)}
                  </span>
                </div>
              ) : null}
              <div className="mt-2 space-y-1">
                {components.map((component, index) => (
                  <div
                    key={index}
                    className="text-xs text-navy-400 flex justify-between border-b border-navy-600/60 py-1"
                  >
                    <span>{component.name}</span>
                    <span className="text-navy-500">{component.calculationDisplay}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="card p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-success flex items-center gap-1.5">
                  <TrendingUp className="w-4 h-4" /> Realized value
                </h3>
                <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex text-xs">
                  <button
                    className={`px-2 py-1 rounded ${realizedMode === 'calculated' ? 'bg-success/20 text-success' : 'text-navy-400'}`}
                    onClick={() => setRealizedMode('calculated')}
                  >
                    Calculate
                  </button>
                  <button
                    className={`px-2 py-1 rounded ${realizedMode === 'override' ? 'bg-warning/20 text-warning' : 'text-navy-400'}`}
                    onClick={() => setRealizedMode('override')}
                  >
                    Override
                  </button>
                </div>
              </div>

              {realizedMode === 'calculated' ? (
                <div className="space-y-2">
                  <p className="text-xs text-navy-500">
                    Enter the actual achieved multiplier per component (defaults to
                    hypothesized). Uses the shared assumptions, so changing a global
                    assumption re-quantifies this too.
                  </p>
                  {components.map((component, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between gap-2 text-xs"
                    >
                      <span className="flex-1 text-navy-300">{component.name}</span>
                      <input
                        id={`uc-actual-${index}`}
                        name={`uc-actual-${index}`}
                        aria-label={`Actual multiplier for ${component.name}`}
                        type="number"
                        step="any"
                        className="input-field w-32 text-right"
                        value={actuals[index] ?? multiplierOf(component)}
                        onChange={(event) =>
                          setActuals((current) => ({
                            ...current,
                            [index]: Number(event.target.value),
                          }))
                        }
                      />
                    </div>
                  ))}
                  <div className="pt-2 border-t border-navy-600 flex justify-between text-sm">
                    <span className="text-navy-400">Calculated realized / yr</span>
                    <span className="text-success font-bold">
                      {fmtMoney(calculatedRealized)}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-navy-500">
                    Manual override — value calculated outside the app (finance model /
                    SFDC). Parameterized inputs stay stored underneath.
                  </p>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-navy-400">$</span>
                    <input
                      id="uc-override-amount"
                      name="uc-override-amount"
                      aria-label="Realized value override amount in millions per year"
                      type="number"
                      step="any"
                      className="input-field"
                      placeholder="Amount ($M/yr)"
                      value={overrideAmount}
                      onChange={(event) =>
                        setOverrideAmount(
                          event.target.value === '' ? '' : Number(event.target.value),
                        )
                      }
                    />
                    <span className="text-xs text-navy-500">M</span>
                  </div>
                  <input
                    id="uc-override-note"
                    name="uc-override-note"
                    aria-label="Realized value override note (source)"
                    className="input-field"
                    placeholder="Note (source)"
                    value={overrideNote}
                    onChange={(event) => setOverrideNote(event.target.value)}
                  />
                </div>
              )}

              <div className="flex items-center justify-between mt-3">
                <div className="text-xs text-navy-400">
                  Current:{' '}
                  <span className="text-white font-medium">
                    {detail.realized && detail.realized.value
                      ? fmtMoney(detail.realized.value)
                      : '—'}
                  </span>
                  {detail.realized ? (
                    <span className="ml-1 text-navy-500">({detail.realized.mode})</span>
                  ) : null}
                </div>
                <button
                  className="btn-primary text-xs"
                  disabled={saveRealized.isPending}
                  onClick={() => saveRealized.mutate()}
                >
                  Save realized
                </button>
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-navy-300 mb-2 flex items-center gap-1.5">
                <Database className="w-4 h-4 text-lava-300" /> Required data assets (
                {(detail.required_assets ?? []).length})
                {detail.requires_locked ? (
                  <span
                    className="badge-muted text-[10px]"
                    title="Module mapping was hand-edited; automatic remapping is disabled for this use case."
                  >
                    manual override
                  </span>
                ) : null}
              </h3>
              <div className="space-y-1">
                {(detail.required_assets ?? []).map((asset) => (
                  <div
                    key={asset.id}
                    className="flex items-center justify-between text-sm border-b border-navy-600 py-1.5"
                  >
                    <span className="text-navy-300">
                      <span className="text-lava-300">{asset.source_system}</span> ·{' '}
                      {asset.module}
                      {asset.criticality === 'helpful' ? (
                        <span className="text-navy-500 text-xs"> (helpful)</span>
                      ) : null}
                    </span>
                    <div className="flex items-center gap-2">
                      <IngestionBadge status={asset.ingestion_status} />
                      {editing ? (
                        <button
                          aria-label={`Remove required module ${asset.module}`}
                          title="Remove this required module"
                          className="text-navy-500 hover:text-lava disabled:opacity-40"
                          disabled={removeRequired.isPending}
                          onClick={() => removeRequired.mutate(asset.id)}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      ) : null}
                    </div>
                  </div>
                ))}
                {(detail.required_assets ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">No required modules.</div>
                ) : null}
              </div>
              {editing ? (
                <>
                  <div className="mt-2 flex items-center gap-2">
                    <select
                      id="uc-add-required-module"
                      name="uc-add-required-module"
                      aria-label="Add a required module"
                      className="input-field text-sm flex-1"
                      value={addModuleId}
                      onChange={(event) =>
                        setAddModuleId(event.target.value ? Number(event.target.value) : '')
                      }
                    >
                      <option value="">Add a required module…</option>
                      {(assetsQuery.data ?? [])
                        .filter(
                          (asset) =>
                            !(detail.required_assets ?? []).some(
                              (required) => required.id === asset.id,
                            ),
                        )
                        .map((asset) => (
                          <option key={asset.id} value={asset.id}>
                            {asset.source_system} · {asset.module}
                          </option>
                        ))}
                    </select>
                    <button
                      className="btn-secondary text-xs whitespace-nowrap disabled:opacity-40"
                      disabled={addModuleId === '' || addRequired.isPending}
                      onClick={() => addModuleId !== '' && addRequired.mutate(addModuleId)}
                    >
                      Add module
                    </button>
                  </div>
                  <p className="text-xs text-navy-500 mt-1.5">
                    Hand-editing modules locks this use case's mapping so automatic
                    remapping won't overwrite your choices.
                  </p>
                </>
              ) : null}
            </section>

            <section className="grid grid-cols-1 gap-3">
              <div>
                <h3 className="text-sm font-semibold text-navy-300 mb-2">
                  Enables (fast-follows)
                </h3>
                {(detail.enables ?? []).map((linked) => (
                  <button
                    key={linked.id}
                    className="w-full text-left flex items-center gap-2 text-sm text-navy-300 hover:text-lava-300 py-1"
                    onClick={() => onOpenUseCase(linked.id)}
                  >
                    <ArrowRight className="w-4 h-4 text-lava shrink-0" />
                    <span className="flex-1">{linked.title}</span>
                    {linked.detected_by_agent ? (
                      <Bot className="w-3.5 h-3.5 text-lava-300" />
                    ) : null}
                  </button>
                ))}
                {(detail.enables ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">None.</div>
                ) : null}
              </div>
              <div>
                <h3 className="text-sm font-semibold text-navy-300 mb-2">
                  Enabled by (prerequisites)
                </h3>
                {(detail.enabled_by ?? []).map((linked) => (
                  <button
                    key={linked.id}
                    className="w-full text-left flex items-center gap-2 text-sm text-navy-300 hover:text-lava-300 py-1"
                    onClick={() => onOpenUseCase(linked.id)}
                  >
                    <ArrowRight className="w-4 h-4 text-navy-500 shrink-0 rotate-180" />
                    <span className="flex-1">{linked.title}</span>
                  </button>
                ))}
                {(detail.enabled_by ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">None.</div>
                ) : null}
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-navy-300 mb-2 flex items-center gap-1.5">
                <MessageSquare className="w-4 h-4 text-lava-300" /> Comments
              </h3>
              <div className="space-y-2 mb-2">
                {(detail.comments ?? []).map((entry) => (
                  <div key={entry.id} className="card p-2.5 text-sm">
                    <div className="text-navy-300">{entry.body}</div>
                    <div className="text-xs text-navy-500 mt-1">{entry.author}</div>
                  </div>
                ))}
                {(detail.comments ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">No comments yet.</div>
                ) : null}
              </div>
              <div className="flex gap-2">
                <input
                  id="uc-comment"
                  name="uc-comment"
                  aria-label="Add a comment"
                  className="input-field"
                  placeholder="Add a comment…"
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && comment.trim()) {
                      postComment.mutate(comment.trim())
                    }
                  }}
                />
                <button
                  className="btn-primary px-3 text-sm"
                  disabled={!comment.trim() || postComment.isPending}
                  onClick={() => comment.trim() && postComment.mutate(comment.trim())}
                >
                  Post
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
