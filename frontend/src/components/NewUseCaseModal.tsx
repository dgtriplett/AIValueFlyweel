// Creating a use case, as three steps rather than one form.
//
// A bare "new use case" form produces an isolated node: no required data, no
// downstream, so nothing on the flywheel moves and the value model is empty. The
// wizard therefore continues past the save — the agent proposes edges you accept
// or reject, and the last step shows what the new node unlocks. That last screen is
// the point of the whole flow: it is where a use case stops being a row in a table.

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, Radar, Sparkles, TrendingUp, X, Zap } from 'lucide-react'

import { api } from '../api'
import { STATUSES, STATUS_LABELS, SUB_VERTICALS, SUB_VERTICAL_LABELS, fmtMoney } from '../constants'
import type {
  Criticality,
  HypothesizedValue,
  Lob,
  Status,
  SubVertical,
  UnlocksResponse,
  UseCase,
} from '../types'

type Step = 'form' | 'detect' | 'unlocks'

interface RequireItem {
  id: number
  label: string
  rationale?: string | null
  accepted: boolean
  criticality?: Criticality | null
}

interface EnableItem {
  id: number
  label: string
  rationale?: string | null
  accepted: boolean
}

const DEFAULT_DRAFT: Partial<UseCase> = {
  title: '',
  description: '',
  lob_id: null,
  sub_vertical: 'cross',
  phase: 2,
  status: 'not_started',
  effort_tshirt: 'M',
  status_source: 'manual',
}

export function NewUseCaseModal({
  lobs,
  onClose,
  onCreated,
  onViewOnFlywheel,
}: {
  lobs: Lob[]
  onClose: () => void
  onCreated: (id: number) => void
  onViewOnFlywheel: (id: number) => void
}): JSX.Element {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>('form')
  const [ucId, setUcId] = useState<number | null>(null)
  const [requires, setRequires] = useState<RequireItem[]>([])
  const [enables, setEnables] = useState<EnableItem[]>([])
  const [model, setModel] = useState('')
  const [unlocks, setUnlocks] = useState<UnlocksResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [valueModel, setValueModel] = useState<HypothesizedValue | null>(null)
  const [draft, setDraft] = useState<Partial<UseCase>>(DEFAULT_DRAFT)

  const create = useMutation({
    mutationFn: (body: Partial<UseCase>) => api.createUseCase(body),
    onSuccess: async (created) => {
      setUcId(created.id)
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      onCreated(created.id)
      setBusy(true)
      try {
        const detected = await api.detectDependencies(created.id)
        setModel(detected.used_llm ? (detected.model ?? '') : 'heuristic')
        setRequires(
          detected.requires.map((edge) => ({
            id: edge.data_asset_id,
            label: edge.label,
            rationale: edge.rationale,
            accepted: true,
            criticality: edge.criticality,
          })),
        )
        setEnables(
          detected.enables.map((edge) => ({
            id: edge.to_use_case_id,
            label: edge.label,
            rationale: edge.rationale,
            accepted: true,
          })),
        )
        setStep('detect')
      } finally {
        setBusy(false)
      }
    },
  })

  // Edges are written one at a time and awaited: each POST changes the readiness of
  // the node it touches, and the unlocks query at the end has to see all of them.
  const applyEdges = async () => {
    if (ucId == null) return
    setBusy(true)
    try {
      for (const item of requires.filter((candidate) => candidate.accepted)) {
        await api.createRequires({
          use_case_id: ucId,
          data_asset_id: item.id,
          criticality: item.criticality || 'required',
        })
      }
      for (const item of enables.filter((candidate) => candidate.accepted)) {
        await api.createEnables({
          from_use_case_id: ucId,
          to_use_case_id: item.id,
          detected_by_agent: true,
          rationale: item.rationale,
        })
      }
      queryClient.invalidateQueries({ queryKey: ['requires'] })
      queryClient.invalidateQueries({ queryKey: ['enables'] })
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      setUnlocks(await api.unlocks(ucId))
      setStep('unlocks')
    } finally {
      setBusy(false)
    }
  }

  const components = valueModel?.components
  const titled = (draft.title ?? '').trim().length > 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative card w-full max-w-lg animate-scale-in max-h-[88vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-bold text-lg flex items-center gap-2">
            {step === 'form' ? 'New Use Case' : null}
            {step === 'detect' ? (
              <>
                <Bot className="w-5 h-5 text-lava" /> AI Dependency Detection
              </>
            ) : null}
            {step === 'unlocks' ? (
              <>
                <Zap className="w-5 h-5 text-success" /> Flywheel Unlock
              </>
            ) : null}
          </h3>
          <button
            aria-label="Close"
            className="text-navy-400 hover:text-white"
            onClick={onClose}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {step === 'form' ? (
          <>
            <div className="space-y-2">
              <input
                id="new-uc-title"
                name="new-uc-title"
                aria-label="Use case title"
                className="input-field"
                placeholder="Title"
                value={draft.title ?? ''}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
              <textarea
                id="new-uc-description"
                name="new-uc-description"
                aria-label="Use case description"
                className="input-field"
                rows={2}
                placeholder="Description"
                value={draft.description ?? ''}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              />
              <div className="grid grid-cols-2 gap-2">
                <select
                  id="new-uc-lob"
                  name="new-uc-lob"
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
                  <option value="">Domain (LOB)…</option>
                  {lobs.map((lob) => (
                    <option key={lob.id} value={lob.id}>
                      {lob.name}
                    </option>
                  ))}
                </select>
                <select
                  id="new-uc-subvertical"
                  name="new-uc-subvertical"
                  aria-label="Generation focus"
                  title="Generation focus"
                  className="input-field"
                  value={draft.sub_vertical ?? ''}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      sub_vertical: (event.target.value as SubVertical) || null,
                    })
                  }
                >
                  <option value="" disabled>
                    Generation focus…
                  </option>
                  {SUB_VERTICALS.map((subVertical) => (
                    <option key={subVertical} value={subVertical}>
                      {SUB_VERTICAL_LABELS[subVertical]}
                    </option>
                  ))}
                </select>
                <select
                  id="new-uc-status"
                  name="new-uc-status"
                  aria-label="Project status"
                  className="input-field"
                  value={draft.status ?? ''}
                  onChange={(event) =>
                    setDraft({ ...draft, status: event.target.value as Status })
                  }
                >
                  {STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {STATUS_LABELS[status]}
                    </option>
                  ))}
                </select>
              </div>

              <p className="text-xs text-navy-500">
                Phase is derived automatically from prerequisite depth once dependencies are set.
              </p>
              <p className="text-xs text-navy-500 flex items-center gap-1">
                <Sparkles className="w-3 h-3 text-lava" /> On create, AI proposes required data +
                downstream use cases, then reveals what this unlocks.
              </p>

              <button
                className="btn-secondary text-xs w-full justify-center"
                disabled={!titled || estimating}
                onClick={async () => {
                  setEstimating(true)
                  try {
                    const estimate = await api.estimateValue({
                      title: draft.title,
                      description: draft.description ?? '',
                    })
                    setValueModel(estimate.value_model)
                    setDraft((current) => ({
                      ...current,
                      hypothesized_value_json: estimate.value_model,
                    }))
                  } finally {
                    setEstimating(false)
                  }
                }}
              >
                <Sparkles className="w-3 h-3" />{' '}
                {estimating ? 'Estimating…' : 'AI: estimate value model'}
              </button>

              {valueModel ? (
                <div className="text-xs text-navy-300 border border-navy-600 rounded p-2">
                  <div className="text-navy-400 mb-1">
                    Proposed value components ({components?.length}):
                  </div>
                  {components?.slice(0, 4).map((component, index) => (
                    <div key={index} className="truncate">
                      · {component.name}{' '}
                      <span className="text-navy-500">
                        ({component.assumptionKeys?.join(' × ')})
                      </span>
                    </div>
                  ))}
                  <div className="text-success mt-1">
                    Attached to this use case — refine in the drawer after creating.
                  </div>
                </div>
              ) : null}
            </div>

            {create.isError ? (
              <div className="text-xs text-lava-300 mt-2">
                Couldn't create the use case. Please try again.
              </div>
            ) : null}

            <div className="flex justify-end gap-2 mt-4">
              <button className="btn-secondary text-sm" onClick={onClose}>
                Cancel
              </button>
              <button
                className="btn-primary text-sm"
                disabled={!titled || create.isPending || busy}
                onClick={() => create.mutate(draft)}
              >
                {busy || create.isPending ? 'Detecting…' : 'Create & detect'}
              </button>
            </div>
          </>
        ) : null}

        {step === 'detect' ? (
          <>
            <p className="text-xs text-navy-400 mb-2">
              Proposed by {model === 'heuristic' ? 'graph heuristic' : model}. Accept or reject
              each, then see the unlock chain.
            </p>

            <div className="mb-3">
              <div className="text-sm font-semibold text-navy-300 mb-1">Requires (data assets)</div>
              {requires.length === 0 ? (
                <div className="text-xs text-navy-500">No strong matches — you can add later.</div>
              ) : null}
              {requires.map((item, index) => (
                <label
                  key={item.id}
                  htmlFor={`new-uc-req-${item.id}`}
                  className="flex items-start gap-2 py-1.5 border-b border-navy-600/60 cursor-pointer"
                >
                  <input
                    id={`new-uc-req-${item.id}`}
                    name={`new-uc-req-${item.id}`}
                    aria-label={`Require data asset ${item.label}`}
                    type="checkbox"
                    checked={item.accepted}
                    onChange={(event) =>
                      setRequires((current) =>
                        current.map((candidate, candidateIndex) =>
                          candidateIndex === index
                            ? { ...candidate, accepted: event.target.checked }
                            : candidate,
                        ),
                      )
                    }
                    className="mt-0.5"
                  />
                  <div className="flex-1">
                    <div className="text-sm text-white">
                      {item.label} <span className="badge-muted text-[10px]">{item.criticality}</span>
                    </div>
                    <div className="text-xs text-navy-500">{item.rationale}</div>
                  </div>
                </label>
              ))}
            </div>

            <div className="mb-3">
              <div className="text-sm font-semibold text-navy-300 mb-1">
                Enables (downstream use cases)
              </div>
              {enables.length === 0 ? (
                <div className="text-xs text-navy-500">No downstream matches detected.</div>
              ) : null}
              {enables.map((item, index) => (
                <label
                  key={item.id}
                  htmlFor={`new-uc-ena-${item.id}`}
                  className="flex items-start gap-2 py-1.5 border-b border-navy-600/60 cursor-pointer"
                >
                  <input
                    id={`new-uc-ena-${item.id}`}
                    name={`new-uc-ena-${item.id}`}
                    aria-label={`Enable downstream use case ${item.label}`}
                    type="checkbox"
                    checked={item.accepted}
                    onChange={(event) =>
                      setEnables((current) =>
                        current.map((candidate, candidateIndex) =>
                          candidateIndex === index
                            ? { ...candidate, accepted: event.target.checked }
                            : candidate,
                        ),
                      )
                    }
                    className="mt-0.5"
                  />
                  <div className="flex-1">
                    <div className="text-sm text-white">{item.label}</div>
                    <div className="text-xs text-navy-500">{item.rationale}</div>
                  </div>
                </label>
              ))}
            </div>

            <div className="flex justify-end gap-2 mt-4">
              <button
                className="btn-secondary text-sm"
                onClick={() => setStep('unlocks')}
                disabled={busy}
              >
                Skip
              </button>
              <button className="btn-primary text-sm" onClick={applyEdges} disabled={busy}>
                <Check className="w-4 h-4" /> {busy ? 'Applying…' : 'Apply & see unlocks'}
              </button>
            </div>
          </>
        ) : null}

        {step === 'unlocks' ? (
          <>
            <div className="card border-l-4 border-l-success mb-3">
              <div className="text-sm text-navy-300">This use case unlocks</div>
              <div className="text-2xl font-bold text-white flex items-center gap-2">
                <Zap className="w-6 h-6 text-success" />
                {unlocks?.summary.count ?? 0} use cases
                <span className="text-sm font-normal text-navy-400">
                  across {unlocks?.summary.lob_count ?? 0} LOBs
                </span>
              </div>
              <div className="text-sm text-lava-300 flex items-center gap-1 mt-1">
                <TrendingUp className="w-4 h-4" /> +
                {fmtMoney(unlocks?.summary.hypothesized_value)} hypothesized value/yr
              </div>
            </div>

            <div className="space-y-1 max-h-56 overflow-y-auto mb-3">
              {(unlocks?.unlocked ?? []).map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between text-sm border-b border-navy-600/60 py-1.5"
                >
                  <span className="text-white">{item.title}</span>
                  <span className="flex items-center gap-2">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                        item.reason === 'enabled' ? 'badge-low' : 'badge-medium'
                      }`}
                    >
                      {item.reason === 'enabled' ? 'builds on this' : 'becomes ready'}
                    </span>
                    <span className="text-lava-300">{fmtMoney(item.value_mm)}</span>
                  </span>
                </div>
              ))}
              {(unlocks?.unlocked.length ?? 0) === 0 ? (
                <div className="text-xs text-navy-500">
                  No downstream unlocks detected yet — accept more edges or land its data.
                </div>
              ) : null}
            </div>

            <div className="flex justify-end gap-2">
              <button className="btn-secondary text-sm" onClick={onClose}>
                Done
              </button>
              <button
                className="btn-primary text-sm"
                onClick={() => {
                  if (ucId != null) onViewOnFlywheel(ucId)
                  onClose()
                }}
              >
                <Radar className="w-4 h-4" /> View on flywheel
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
