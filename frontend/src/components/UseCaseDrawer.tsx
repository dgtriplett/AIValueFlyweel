// The use-case detail: read, edit, advance, quantify, and link.
//
// ONE source of truth, TWO layouts. `UseCaseDetail` owns all the data-fetching,
// mutations and section rendering; `UseCaseDrawer` and `UseCaseDetailPage` are thin
// wrappers that only choose the chrome around it.
//
// The drawer slides over the current view because you nearly always open one FROM a
// table or the flywheel and want to go straight back. The full page (feedback item
// A) is the Portfolio Manager's primary workspace: the drawer got cramped once we
// grew the progression / notes / status-history content, so an expand affordance in
// the drawer header switches the SAME use case into a wide two-column layout with a
// breadcrumb back to where you were.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Calendar,
  Database,
  Maximize2,
  MessageSquare,
  Pencil,
  Save,
  TrendingUp,
  User,
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

/** Which chrome wraps the shared detail body. */
type DetailLayout = 'drawer' | 'page'

interface UseCaseDetailProps {
  ucId: number
  lobs: Lob[]
  onClose: () => void
  onOpenUseCase: (id: number) => void
  onOpenDataAsset: (id: number) => void
  onWriteProposal: (id: number) => void
  /** 'drawer' (default slide-over) or 'page' (full-page workspace). */
  layout?: DetailLayout
  /** Hide every edit control and mutation trigger (executive persona). */
  readOnly?: boolean
  /** Drawer only: switch this use case into the full-page layout. */
  onExpand?: () => void
  /** Page only: breadcrumb/back to where the user came from. */
  onBack?: () => void
}

/**
 * The shared use-case detail body — all state, queries, mutations and sections.
 * Rendered inside a slide-over by {@link UseCaseDrawer} and inside a full page by
 * {@link UseCaseDetailPage}. There is deliberately no second copy of the
 * progression / asset / value logic: the layout differs, the behaviour does not.
 */
function UseCaseDetail({
  ucId,
  lobs,
  onClose,
  onOpenUseCase,
  onOpenDataAsset,
  onWriteProposal,
  layout = 'drawer',
  readOnly = false,
  onExpand,
  onBack,
}: UseCaseDetailProps) {
  const isPage = layout === 'page'
  const queryClient = useQueryClient()
  const {
    data: detail,
    isLoading,
    isError,
  } = useQuery({ queryKey: ['uc-detail', ucId], queryFn: () => api.useCaseDetail(ucId) })
  const assumptionsQuery = useQuery({ queryKey: ['assumptions'], queryFn: api.assumptions })
  const assetsQuery = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })
  const useCasesQuery = useQuery({
    queryKey: ['use-cases', 'all'],
    queryFn: () => api.useCases('all'),
  })

  const [editing, setEditing] = useState(false)
  const [addModuleId, setAddModuleId] = useState<number | ''>('')
  const [addEnabledId, setAddEnabledId] = useState<number | ''>('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [comment, setComment] = useState('')
  const [realizedMode, setRealizedMode] = useState<'calculated' | 'override'>('calculated')
  const [actuals, setActuals] = useState<Record<number, number>>({})
  const [overrideAmount, setOverrideAmount] = useState<number | ''>('')
  const [overrideNote, setOverrideNote] = useState('')
  // Hypothesized editor state — mirrors the realized editor exactly. CALCULATE mode
  // edits per-component multipliers (hypActuals) with a live total; OVERRIDE mode
  // takes a straight dollar value plus a required 'why I am overriding' note.
  const [hypMode, setHypMode] = useState<'calculated' | 'override'>('calculated')
  const [hypActuals, setHypActuals] = useState<Record<number, number>>({})
  const [hypOverrideAmount, setHypOverrideAmount] = useState<number | ''>('')
  const [hypOverrideNote, setHypOverrideNote] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [slippageReason, setSlippageReason] = useState('')
  const [progressionNote, setProgressionNote] = useState('')
  const [showSlippagePrompt, setShowSlippagePrompt] = useState(false)

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
    // Hypothesized editor — seed from the hypothesized components + override columns.
    setHypMode(detail.hypothesized_override_enabled ? 'override' : 'calculated')
    setHypOverrideAmount(detail.hypothesized_override_amount ?? '')
    setHypOverrideNote(detail.hypothesized_override_note ?? '')
    const hypComponents = detail.hypothesized_value_json?.components ?? []
    const nextHyp: Record<number, number> = {}
    hypComponents.forEach((component, index) => {
      nextHyp[index] = multiplierOf(component)
    })
    setHypActuals(nextHyp)
    // Reset progression state
    setTargetDate(detail.progression?.target_go_live_date ?? '')
    setProgressionNote('')
    setSlippageReason('')
    setShowSlippagePrompt(false)
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

  const invalidateEnables = () => {
    invalidateDetail()
    queryClient.invalidateQueries({ queryKey: ['enables'] })
    queryClient.invalidateQueries({ queryKey: ['blast'] })
  }

  const addEnabled = useMutation({
    mutationFn: (toUseCaseId: number) =>
      api.createEnables({
        from_use_case_id: ucId,
        to_use_case_id: toUseCaseId,
      }),
    onSuccess: () => {
      setAddEnabledId('')
      invalidateEnables()
    },
  })

  const removeEnabled = useMutation({
    mutationFn: (toUseCaseId: number) => api.deleteEnables(ucId, toUseCaseId),
    onSuccess: invalidateEnables,
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

  const saveHypothesized = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error('no use case loaded')
      const components = detail.hypothesized_value_json?.components ?? []
      // Rebuild hypothesized_value_json from the edited multipliers, preserving the
      // same shape {driver, components:[{name, calculationDisplay, multiplier, assumptionKeys}]}.
      return api.updateUseCase(ucId, {
        title: detail.title,
        hypothesized_value_json: {
          driver: detail.hypothesized_value_json?.driver ?? 'Hypothesized',
          components: components.map((component, index) => ({
            name: component.name,
            calculationDisplay: component.calculationDisplay,
            multiplier: hypActuals[index] ?? multiplierOf(component),
            assumptionKeys: component.assumptionKeys,
          })),
        },
        hypothesized_override_enabled: hypMode === 'override',
        hypothesized_override_amount:
          hypOverrideAmount === '' ? null : Number(hypOverrideAmount),
        hypothesized_override_note: hypOverrideNote || null,
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

  const setTargetDateMutation = useMutation({
    mutationFn: ({ date, reason }: { date: string | null; reason?: string }) =>
      api.setTargetDate(ucId, date, reason),
    onSuccess: () => {
      setTargetDate('')
      setSlippageReason('')
      setShowSlippagePrompt(false)
      queryClient.invalidateQueries({ queryKey: ['uc-detail', ucId] })
    },
  })

  const addProgressionNoteMutation = useMutation({
    mutationFn: (note: string) => api.addProgressionNote(ucId, note),
    onSuccess: () => {
      setProgressionNote('')
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

  // Live hypothesized total — same math as calculatedRealized but driven by the
  // hypothesized multiplier edits (hypActuals). Recomputed from shared assumptions.
  const calculatedHypothesized = components.reduce((total, component, index) => {
    let value = hypActuals[index] ?? multiplierOf(component)
    for (const key of component.assumptionKeys ?? []) value *= assumptionValues[key] ?? 0
    return total + value
  }, 0)

  // Milestone / stage indicator (feedback item A.3b): a clearer read of where this
  // use case sits in its lifecycle, built from the EXISTING status field and the
  // canonical STATUSES ordering — no new backend field. Rendered prominently in the
  // full-page tracking banner.
  const statusIndex = detail?.status ? STATUSES.indexOf(detail.status) : -1
  const milestoneStep = statusIndex >= 0 ? statusIndex + 1 : 0

  // Owner/assignee (feedback item A.3a): the data model exposes `created_by` but has
  // no dedicated owner/assignee column, so we SURFACE the author read-only rather
  // than fabricate a field.
  // TODO: add an editable owner/assignee once the API/UseCase type carries one
  //       (needs a server column + migration — deliberately out of scope here).
  const owner = detail?.created_by ?? null

  // The header chrome differs by layout: a full-page view leads with a breadcrumb
  // back to where the user was and no overlay Close; the drawer keeps its Close and
  // gains the Expand affordance that opens this same use case full-page.
  const header = (
    <div
      className={
        isPage
          ? 'sticky top-0 bg-navy-800/95 backdrop-blur border-b border-navy-600 px-6 py-3 flex items-center justify-between z-10'
          : 'sticky top-0 bg-navy-800/95 backdrop-blur border-b border-navy-600 px-5 py-3 flex items-center justify-between z-10'
      }
    >
      <div className="flex items-center gap-2">
        {isPage && onBack ? (
          <button
            data-ga-uc-back="1"
            className="text-navy-400 hover:text-white flex items-center gap-1 text-sm"
            onClick={onBack}
          >
            <ArrowLeft className="w-4 h-4" /> Back
          </button>
        ) : null}
        <span className="text-xs text-navy-500">Use Case #{ucId}</span>
        {detail ? (
          <ReadinessBadge
            readiness={detail.readiness}
            pendingPrereqs={detail.pending_prereqs}
          />
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        {readOnly ? (
          <span className="text-xs text-navy-500" title="Executive view is read-only">
            Read-only
          </span>
        ) : editing ? (
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
            action belongs here and stays in the SPA with the id pre-filled. */}
        {ucId ? (
          // Hidden in the read-only executive variant; the pinned parity string
          // `{ucId ? (` is kept so the source-to-bundle gate still matches.
          !readOnly ? (
            <button
              data-gaProposalBtn="1"
              title="Generate an eight-section proposal for this use case, grounded in its computed value, its real data gaps and this instance’s company profile"
              className="text-navy-400 hover:text-lava-300 flex items-center gap-1 text-sm"
              onClick={() => onWriteProposal(ucId)}
            >
              ✎ Write proposal
            </button>
          ) : null
        ) : null}
        {/* Feedback item A: expand the cramped drawer into the full-page workspace,
            carrying the SAME ucId. Drawer only — there is nothing to expand TO from
            the page itself. */}
        {!isPage && onExpand ? (
          <button
            data-ga-uc-expand="1"
            aria-label="Expand to full page"
            title="Expand to full page"
            className="text-navy-400 hover:text-lava-300 flex items-center gap-1 text-sm"
            onClick={onExpand}
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        ) : null}
        {!isPage ? (
          <button
            aria-label="Close"
            className="text-navy-400 hover:text-white"
            onClick={onClose}
          >
            <X className="w-5 h-5" />
          </button>
        ) : null}
      </div>
    </div>
  )

  const body =
    isError ? (
      <div className="p-6 text-lava-300 text-sm">
        Couldn't load this use case. Please close and try again.
      </div>
    ) : isLoading || !detail || !draft ? (
      <div className="p-6 text-navy-400">Loading…</div>
    ) : (
      <div className={isPage ? 'p-6 space-y-5' : 'p-5 space-y-5'}>
            {/* Feedback item A.2: on the full page, surface the tracking signals the
                cramped drawer buried — go-live target, at-risk, owner and the
                milestone/stage — prominently, above the fold. */}
            {isPage ? (
              <div
                data-ga-uc-tracking="1"
                className="card p-4 grid grid-cols-2 md:grid-cols-4 gap-4"
              >
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-navy-500">
                    Target go-live
                  </div>
                  <div className="text-sm font-semibold text-white mt-0.5">
                    {detail.progression?.target_go_live_date ?? '—'}
                  </div>
                  {detail.progression?.at_risk ? (
                    <span className="text-lava text-[10px] font-semibold">⚠ AT RISK</span>
                  ) : null}
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-navy-500">
                    Milestone
                  </div>
                  <div className="text-sm font-semibold text-white mt-0.5">
                    {detail.status ? STATUS_LABELS[detail.status] : '—'}
                  </div>
                  {milestoneStep > 0 ? (
                    <div className="mt-1.5 flex items-center gap-1" aria-hidden="true">
                      {STATUSES.map((value, index) => (
                        <span
                          key={value}
                          title={STATUS_LABELS[value]}
                          className={`h-1.5 flex-1 rounded-full ${
                            index < milestoneStep ? 'bg-success' : 'bg-navy-600'
                          }`}
                        />
                      ))}
                    </div>
                  ) : null}
                  <div className="text-[10px] text-navy-500 mt-0.5">
                    Stage {milestoneStep} of {STATUSES.length}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-navy-500">
                    Owner
                  </div>
                  <div className="text-sm font-semibold text-white mt-0.5 flex items-center gap-1">
                    <User className="w-3.5 h-3.5 text-navy-400" />
                    {owner ?? 'Unassigned'}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-navy-500">
                    Realized / yr
                  </div>
                  <div className="text-sm font-semibold text-success mt-0.5">
                    {detail.realized && detail.realized.value
                      ? fmtMoney(detail.realized.value)
                      : '—'}
                  </div>
                </div>
              </div>
            ) : null}
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
                            disabled={readOnly || advance.isPending}
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
                        disabled={readOnly || setPortfolio.isPending}
                        title="In your portfolio — click to remove"
                        onClick={() => setPortfolio.mutate(false)}
                      >
                        In portfolio
                      </button>
                    </>
                  ) : (
                    <button
                      className="badge-muted inline-flex items-center gap-1 hover:text-white hover:border-info disabled:opacity-50"
                      disabled={readOnly || setPortfolio.isPending}
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
                {detail.required_ready}/{detail.required_total} required {detail.requirement_model === 'domain' ? 'data domains satisfied' : 'assets ready'}
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

            <section className="card p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-lava-300 flex items-center gap-1.5">
                  <TrendingUp className="w-4 h-4" /> Hypothesized value
                </h3>
                <div className="bg-navy-700 border border-navy-600 rounded p-0.5 flex text-xs">
                  <button
                    className={`px-2 py-1 rounded ${hypMode === 'calculated' ? 'bg-lava-300/20 text-lava-300' : 'text-navy-400'}`}
                    onClick={() => setHypMode('calculated')}
                  >
                    Calculate
                  </button>
                  <button
                    className={`px-2 py-1 rounded ${hypMode === 'override' ? 'bg-warning/20 text-warning' : 'text-navy-400'}`}
                    onClick={() => setHypMode('override')}
                  >
                    Override
                  </button>
                </div>
              </div>

              {components.length === 0 ? (
                // No value model yet - show estimate button
                <div className="space-y-3 py-4">
                  <p className="text-sm text-navy-400">
                    No value model yet. Generate an initial estimate to unlock the
                    Calculate and Override features.
                  </p>
                  <button
                    className="btn-primary text-sm"
                    onClick={async () => {
                      if (!detail?.id) return
                      try {
                        await api.estimateUseCaseValue(detail.id)
                        // Invalidate the detail query to refetch with new hypothesized_value_json
                        queryClient.invalidateQueries({ queryKey: ['use-case-detail', detail.id] })
                      } catch (err) {
                        console.error('Failed to estimate value:', err)
                      }
                    }}
                  >
                    Estimate value
                  </button>
                </div>
              ) : (
                <>
              {detail.value_range ? (
                <div className="text-sm text-navy-300 mb-2">
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

              {hypMode === 'calculated' ? (
                <div className="space-y-2">
                  <p className="text-xs text-navy-500">
                    Enter the hypothesized multiplier per component. Uses the shared
                    assumptions, so changing a global assumption re-quantifies this too.
                  </p>
                  {components.map((component, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between gap-2 text-xs"
                    >
                      <span className="flex-1 text-navy-300">
                        {component.name}
                        <span className="block text-navy-500">
                          {component.calculationDisplay}
                        </span>
                      </span>
                      <input
                        id={`uc-hyp-actual-${index}`}
                        name={`uc-hyp-actual-${index}`}
                        aria-label={`Hypothesized multiplier for ${component.name}`}
                        type="number"
                        step="any"
                        className="input-field w-32 text-right"
                        value={hypActuals[index] ?? multiplierOf(component)}
                        onChange={(event) =>
                          setHypActuals((current) => ({
                            ...current,
                            [index]: Number(event.target.value),
                          }))
                        }
                      />
                    </div>
                  ))}
                  <div className="pt-2 border-t border-navy-600 flex justify-between text-sm">
                    <span className="text-navy-400">Calculated hypothesized / yr</span>
                    <span className="text-lava-300 font-bold">
                      {fmtMoney(calculatedHypothesized)}
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
                      id="uc-hyp-override-amount"
                      name="uc-hyp-override-amount"
                      aria-label="Hypothesized value override amount in millions per year"
                      type="number"
                      step="any"
                      className="input-field"
                      placeholder="Amount ($M/yr)"
                      value={hypOverrideAmount}
                      onChange={(event) =>
                        setHypOverrideAmount(
                          event.target.value === '' ? '' : Number(event.target.value),
                        )
                      }
                    />
                    <span className="text-xs text-navy-500">M</span>
                  </div>
                  <textarea
                    id="uc-hyp-override-note"
                    name="uc-hyp-override-note"
                    aria-label="Why I am overriding the hypothesized value (required)"
                    className="input-field"
                    rows={2}
                    placeholder="Why I am overriding (required)"
                    value={hypOverrideNote}
                    onChange={(event) => setHypOverrideNote(event.target.value)}
                  />
                  {hypMode === 'override' && !hypOverrideNote.trim() ? (
                    <p className="text-xs text-warning">
                      A note explaining the override is required.
                    </p>
                  ) : null}
                </div>
              )}

              <div className="flex items-center justify-end mt-3">
                <button
                  className="btn-primary text-xs"
                  disabled={
                    readOnly ||
                    saveHypothesized.isPending ||
                    (hypMode === 'override' && !hypOverrideNote.trim())
                  }
                  onClick={() => saveHypothesized.mutate()}
                >
                  Save hypothesized
                </button>
              </div>
              </>
              )}
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
                  disabled={readOnly || saveRealized.isPending}
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
                {detail.requirement_model === 'domain' && detail.required_domains?.length ? (
                  // Domain-path: group serving assets by domain
                  detail.required_domains.map((domain) => (
                    <div key={domain.domain_id} className="space-y-0.5">
                      <div className="text-xs font-semibold text-navy-400 px-1 py-0.5 flex items-center gap-2">
                        <span>{domain.domain_name}</span>
                        <span
                          className={`badge-${domain.satisfied ? 'high' : 'muted'} text-[10px]`}
                        >
                          {domain.satisfied ? 'covered' : 'pending'}
                        </span>
                      </div>
                      {domain.serving_assets.map((asset) => (
                        <button
                          key={asset.id}
                          className="w-full flex items-center justify-between text-sm border-b border-navy-600 py-1.5 hover:bg-navy-700/50 px-1 rounded text-left ml-3"
                          onClick={() => onOpenDataAsset(asset.id)}
                        >
                          <span className="text-navy-300 text-xs">
                            <span className="text-lava-300">{asset.source_system}</span> ·{" "}
                            {asset.module}
                            {domain.satisfied && asset.ingestion_status !== 'governed' && asset.ingestion_status !== 'curated' ? (
                              <span className="ml-2 text-[10px] text-navy-500">(domain covered)</span>
                            ) : null}
                          </span>
                          <IngestionBadge status={asset.ingestion_status} />
                        </button>
                      ))}
                    </div>
                  ))
                ) : (
                  // Module-path or fallback: flat list (existing behavior)
                  (detail.required_assets ?? []).map((asset) => (
                    <button
                      key={asset.id}
                      className="w-full flex items-center justify-between text-sm border-b border-navy-600 py-1.5 hover:bg-navy-700/50 px-1 rounded text-left"
                      onClick={() => onOpenDataAsset(asset.id)}
                    >
                      <span className="text-navy-300">
                        <span className="text-lava-300">{asset.source_system}</span> ·{" "}
                        {asset.module}
                      </span>
                      <div className="flex items-center gap-2">
                        <IngestionBadge status={asset.ingestion_status} />
                        {editing ? (
                          <button
                            aria-label={`Remove required module ${asset.module}`}
                            title="Remove this required module"
                            className="text-navy-500 hover:text-lava disabled:opacity-40"
                            disabled={removeRequired.isPending}
                            onClick={(e) => {
                              e.stopPropagation()
                              removeRequired.mutate(asset.id)
                            }}
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </button>
                  ))
                )}
                {(detail.required_assets ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">No required modules.</div>
                ) : null}
              </div>
                  </button>
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
                            ) &&
                            !(detail.helpful_assets ?? []).some(
                              (helpful) => helpful.id === asset.id,
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

            {(detail.helpful_assets ?? []).length > 0 ? (
              <section>
                <h3 className="text-sm font-semibold text-navy-300 mb-2 flex items-center gap-1.5">
                  <Database className="w-4 h-4 text-navy-500" /> Helpful data assets (
                  {(detail.helpful_assets ?? []).length})
                </h3>
                <div className="space-y-1">
                  {(detail.helpful_assets ?? []).map((asset) => (
                    <button
                      key={asset.id}
                      className="w-full flex items-center justify-between text-sm border-b border-navy-600 py-1.5 hover:bg-navy-700/50 px-1 rounded text-left"
                      onClick={() => onOpenDataAsset(asset.id)}
                    >
                      <span className="text-navy-400">
                        <span className="text-navy-500">{asset.source_system}</span> ·{' '}
                        {asset.module}
                        <span className="text-navy-500 text-xs ml-1">(helpful)</span>
                      </span>
                      <div className="flex items-center gap-2">
                        <IngestionBadge status={asset.ingestion_status} />
                        {editing ? (
                          <button
                            aria-label={`Remove helpful module ${asset.module}`}
                            title="Remove this helpful module"
                            className="text-navy-500 hover:text-lava disabled:opacity-40"
                            disabled={removeRequired.isPending}
                            onClick={(e) => {
                              e.stopPropagation()
                              removeRequired.mutate(asset.id)
                            }}
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </button>
                  ))}
                </div>
              </section>
            ) : null}

            <section className="grid grid-cols-1 gap-3">
              <div>
                <h3 className="text-sm font-semibold text-navy-300 mb-2">
                  Enables (fast-follows)
                </h3>
                {(detail.enables ?? []).map((linked) => (
                  <div
                    key={linked.id}
                    className="flex items-center gap-2 text-sm py-1"
                  >
                    <button
                      className="min-w-0 flex-1 text-left flex items-center gap-2 text-navy-300 hover:text-lava-300"
                      onClick={() => onOpenUseCase(linked.id)}
                    >
                      <ArrowRight className="w-4 h-4 text-lava shrink-0" />
                      <span className="flex-1 truncate">{linked.title}</span>
                      {linked.detected_by_agent ? (
                        <Bot className="w-3.5 h-3.5 text-lava-300 shrink-0" />
                      ) : null}
                    </button>
                    {editing ? (
                      <button
                        aria-label={`Remove enabled use case ${linked.title}`}
                        title="Remove this enabled use case"
                        className="text-navy-500 hover:text-lava disabled:opacity-40"
                        disabled={removeEnabled.isPending}
                        onClick={() => removeEnabled.mutate(linked.id)}
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    ) : null}
                  </div>
                ))}
                {(detail.enables ?? []).length === 0 ? (
                  <div className="text-xs text-navy-500">None.</div>
                ) : null}
                {editing ? (
                  <div className="mt-2 flex items-center gap-2">
                    <select
                      id="uc-add-enabled-use-case"
                      name="uc-add-enabled-use-case"
                      aria-label="Add an enabled use case"
                      className="input-field text-sm flex-1 min-w-0"
                      value={addEnabledId}
                      onChange={(event) =>
                        setAddEnabledId(event.target.value ? Number(event.target.value) : '')
                      }
                    >
                      <option value="">Add an enabled use case…</option>
                      {(useCasesQuery.data ?? [])
                        .filter(
                          (useCase) =>
                            useCase.id !== ucId &&
                            !(detail.enables ?? []).some((enabled) => enabled.id === useCase.id),
                        )
                        .map((useCase) => (
                          <option key={useCase.id} value={useCase.id}>
                            {useCase.title}
                          </option>
                        ))}
                    </select>
                    <button
                      className="btn-secondary text-xs whitespace-nowrap disabled:opacity-40"
                      disabled={addEnabledId === '' || addEnabled.isPending}
                      onClick={() => addEnabledId !== '' && addEnabled.mutate(addEnabledId)}
                    >
                      Add use case
                    </button>
                  </div>
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
                <Calendar className="w-4 h-4 text-lava-300" /> Progression & Timeline
              </h3>
              <div
                className={
                  isPage
                    ? 'card p-4 grid grid-cols-1 lg:grid-cols-2 gap-4'
                    : 'card p-4 space-y-3'
                }
              >
                <div className="space-y-3">
                <div className="space-y-2">
                  <label htmlFor="uc-target-date" className="text-xs text-navy-400 block">
                    Target go-live date
                    {detail.progression?.at_risk ? (
                      <span className="ml-2 text-lava text-[10px] font-semibold">⚠ AT RISK</span>
                    ) : null}
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      id="uc-target-date"
                      name="uc-target-date"
                      type="date"
                      className="input-field flex-1"
                      value={targetDate}
                      onChange={(event) => {
                        const newDate = event.target.value
                        const current = detail.progression?.target_go_live_date
                        // Check for slippage: new date > current date
                        if (current && newDate && newDate > current) {
                          setTargetDate(newDate)
                          setShowSlippagePrompt(true)
                        } else {
                          setTargetDate(newDate)
                          setShowSlippagePrompt(false)
                        }
                      }}
                    />
                    <button
                      className="btn-secondary text-xs whitespace-nowrap disabled:opacity-40"
                      disabled={
                        readOnly ||
                        setTargetDateMutation.isPending ||
                        targetDate === (detail.progression?.target_go_live_date ?? '')
                      }
                      onClick={() => {
                        if (showSlippagePrompt) {
                          // Don't submit yet — prompt for reason below
                          return
                        }
                        setTargetDateMutation.mutate({
                          date: targetDate || null,
                        })
                      }}
                    >
                      {targetDate ? 'Update' : 'Clear'}
                    </button>
                  </div>
                  {!detail.progression?.target_go_live_date && !targetDate ? (
                    <div className="text-xs text-navy-500">No target date set.</div>
                  ) : null}
                </div>

                {showSlippagePrompt ? (
                  <div className="border border-warning/30 bg-warning/10 rounded p-3 space-y-2">
                    <div className="text-xs text-warning font-semibold">
                      ⚠ Date is moving later — please explain why:
                    </div>
                    <textarea
                      id="uc-slippage-reason"
                      name="uc-slippage-reason"
                      className="input-field"
                      rows={2}
                      placeholder="Reason for slippage (e.g., 'upstream dependency delayed', 'scope expanded')…"
                      value={slippageReason}
                      onChange={(event) => setSlippageReason(event.target.value)}
                    />
                    <div className="flex gap-2">
                      <button
                        className="btn-primary text-xs disabled:opacity-40"
                        disabled={
                          !slippageReason.trim() || setTargetDateMutation.isPending
                        }
                        onClick={() => {
                          setTargetDateMutation.mutate({
                            date: targetDate,
                            reason: slippageReason.trim(),
                          })
                        }}
                      >
                        Confirm slippage
                      </button>
                      <button
                        className="btn-secondary text-xs"
                        onClick={() => {
                          setTargetDate(detail.progression?.target_go_live_date ?? '')
                          setSlippageReason('')
                          setShowSlippagePrompt(false)
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
                </div>

                <div className={isPage ? '' : 'border-t border-navy-600 pt-3'}>
                  <h4 className="text-xs text-navy-400 mb-2">History</h4>
                  <div
                    className={
                      isPage
                        ? 'space-y-1.5 max-h-[28rem] overflow-y-auto pr-1'
                        : 'space-y-1.5 max-h-60 overflow-y-auto'
                    }
                  >
                    {(detail.progression?.events ?? []).map((event) => (
                      <div
                        key={event.id}
                        className="text-xs border-b border-navy-600/40 pb-1.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 space-y-0.5">
                            {event.event_type === 'date_set' ? (
                              <div className="text-navy-300">
                                <span className="text-info">Target set:</span> {event.to_value}
                              </div>
                            ) : event.event_type === 'date_change' ? (
                              <div className="text-navy-300">
                                <span
                                  className={
                                    event.to_value && event.from_value && event.to_value > event.from_value
                                      ? 'text-warning'
                                      : 'text-info'
                                  }
                                >
                                  {event.to_value && event.from_value && event.to_value > event.from_value
                                    ? '⚠ Slipped:'
                                    : 'Date changed:'}
                                </span>{' '}
                                {event.from_value} → {event.to_value}
                              </div>
                            ) : event.event_type === 'date_cleared' ? (
                              <div className="text-navy-300">
                                <span className="text-navy-500">Target cleared</span> (was{' '}
                                {event.from_value})
                              </div>
                            ) : event.event_type === 'status_change' ? (
                              <div className="text-navy-300">
                                <span className="text-success">Status:</span> {event.from_value} →{' '}
                                {event.to_value}
                              </div>
                            ) : event.event_type === 'note' ? (
                              <div className="text-navy-300">
                                <span className="text-lava-300">Note:</span> {event.note}
                              </div>
                            ) : null}
                            {event.note && event.event_type !== 'note' ? (
                              <div className="text-navy-500 italic text-[11px]">
                                {event.note}
                              </div>
                            ) : null}
                          </div>
                          <div className="text-navy-500 text-[10px] shrink-0">
                            {event.created_at
                              ? new Date(event.created_at).toLocaleDateString()
                              : ''}
                          </div>
                        </div>
                        {event.created_by ? (
                          <div className="text-navy-600 text-[10px] mt-0.5">
                            {event.created_by}
                          </div>
                        ) : null}
                      </div>
                    ))}
                    {(detail.progression?.events ?? []).length === 0 ? (
                      <div className="text-navy-500">No history yet.</div>
                    ) : null}
                  </div>
                </div>

                <div
                  className={
                    isPage
                      ? 'lg:col-span-2 border-t border-navy-600 pt-3'
                      : 'border-t border-navy-600 pt-3'
                  }
                >
                  <h4 className="text-xs text-navy-400 mb-2">Add a note</h4>
                  <div className="flex gap-2">
                    <input
                      id="uc-progression-note"
                      name="uc-progression-note"
                      className="input-field text-sm flex-1"
                      placeholder="Status update, milestone, blocker…"
                      value={progressionNote}
                      onChange={(event) => setProgressionNote(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && progressionNote.trim()) {
                          addProgressionNoteMutation.mutate(progressionNote.trim())
                        }
                      }}
                    />
                    <button
                      className="btn-primary px-3 text-sm whitespace-nowrap disabled:opacity-40"
                      disabled={
                        readOnly || !progressionNote.trim() || addProgressionNoteMutation.isPending
                      }
                      onClick={() =>
                        progressionNote.trim() &&
                        addProgressionNoteMutation.mutate(progressionNote.trim())
                      }
                    >
                      Add
                    </button>
                  </div>
                </div>
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
                  disabled={readOnly || !comment.trim() || postComment.isPending}
                  onClick={() => comment.trim() && postComment.mutate(comment.trim())}
                >
                  Post
                </button>
              </div>
            </section>
          </div>
    )

  if (isPage) {
    // The full-page workspace: a wide, scrollable surface that the App renders in
    // place of the active view. The extra real estate is spent on tracking — the
    // Progression & Timeline / notes / status history get their own wide column.
    return (
      <div
        data-ga-uc-page="1"
        className="fixed inset-0 z-40 bg-navy-900 overflow-y-auto"
      >
        <div className="mx-auto max-w-[1200px]">
          {header}
          {body}
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-navy-800 border-l border-navy-600 overflow-y-auto animate-in slide-in-from-right shadow-card-hover">
        {header}
        {body}
      </div>
    </div>
  )
}

/**
 * The slide-over drawer — the default way to inspect a use case from a table or the
 * flywheel. `onExpand` (feedback item A) switches this same use case into the
 * full-page workspace.
 */
export function UseCaseDrawer(props: {
  ucId: number
  lobs: Lob[]
  onClose: () => void
  onOpenUseCase: (id: number) => void
  onOpenDataAsset: (id: number) => void
  onWriteProposal: (id: number) => void
  onExpand?: () => void
  readOnly?: boolean
}) {
  return <UseCaseDetail {...props} layout="drawer" />
}

/**
 * The full-page use-case workspace (feedback item A) — the Portfolio Manager's
 * primary surface. Reuses every query, mutation and section of the drawer; only the
 * chrome (breadcrumb back, wider two-column body) differs. `onBack` returns the user
 * to wherever they expanded from.
 */
export function UseCaseDetailPage(props: {
  ucId: number
  lobs: Lob[]
  onBack: () => void
  onOpenUseCase: (id: number) => void
  onOpenDataAsset: (id: number) => void
  onWriteProposal: (id: number) => void
  readOnly?: boolean
}) {
  return (
    <UseCaseDetail
      {...props}
      layout="page"
      // The page has no overlay Close; Back IS the way out, so route both here.
      onClose={props.onBack}
    />
  )
}
