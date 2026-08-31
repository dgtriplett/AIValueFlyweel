import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'

import { api } from '../api'
import { Banner } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { useApiErrorToast } from '../components/Toasts'
import { OPTION_STYLE, SELECT_CLASS } from '../constants'
import { NO_RETRY } from '../lib/retry'
import type {
  ConfirmCardData,
  GenerateUseCasesInput,
  GenerateUseCasesResponse,
} from '../types'

const INPUT_CLASS = `${SELECT_CLASS} w-full`

export function generationConfirmation(input: GenerateUseCasesInput): ConfirmCardData {
  return {
    token: 'generate-use-cases',
    intent: 'generate_use_cases',
    summary: `Generate ${input.count} use-case candidate${input.count === 1 ? '' : 's'} with the LLM. Nothing will be saved yet.`,
    before: { candidates: 0 },
    after: { candidates: input.count, lens: input.lens },
  }
}

export default function GenerateView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const lobs = useQuery({ queryKey: ['lobs'], queryFn: api.lobs })
  const [input, setInput] = useState<GenerateUseCasesInput>({
    lob_id: null,
    lens: 'both',
    count: 6,
    time_horizon_bias: null,
    prioritize_regulatory: false,
  })
  const [approveGeneration, setApproveGeneration] = useState(false)
  const [preview, setPreview] = useState<GenerateUseCasesResponse | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [inPortfolio, setInPortfolio] = useState(true)
  const [confirm, setConfirm] = useState<ConfirmCardData | null>(null)

  const generate = useMutation({
    mutationFn: () => api.generateUseCases(input),
    ...NO_RETRY,
    onSuccess: (response) => {
      setPreview(response)
      setSelected(new Set(response.candidates.map((candidate) => candidate.candidate_id)))
      setConfirm(null)
      setApproveGeneration(false)
    },
  })

  const prepare = useMutation({
    mutationFn: () =>
      api.prepareGeneratedUseCases({
        preview_id: preview!.preview_id,
        candidate_ids: Array.from(selected),
        in_portfolio: inPortfolio,
      }),
    ...NO_RETRY,
    onSuccess: setConfirm,
    onError: (error) => reportError(error, 'Could not prepare the selected use cases.'),
  })

  const candidates = preview?.candidates ?? []
  const summary = preview?.summary
  const generationCard = useMemo(() => generationConfirmation(input), [input])

  const invalidatePortfolio = () => {
    queryClient.invalidateQueries({ queryKey: ['use-cases'] })
    queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
    queryClient.invalidateQueries({ queryKey: ['coverage-matrix'] })
    queryClient.invalidateQueries({ queryKey: ['roadmap-items'] })
    setPreview(null)
    setConfirm(null)
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Generate use cases</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          Propose new use cases grounded in the data you actually have. <strong>Ready</strong>{' '}
          means buildable today; <strong>gap</strong> makes the case for landing new data.
          Nothing is saved until you approve it.
        </p>
      </div>

      <div className="card space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <label>
            <span className="text-xs text-navy-400 block mb-1">Line of business</span>
            <select
              className={INPUT_CLASS}
              value={input.lob_id ?? ''}
              onChange={(event) =>
                setInput({ ...input, lob_id: event.target.value ? Number(event.target.value) : null })
              }
            >
              <option value="" style={OPTION_STYLE}>All</option>
              {(lobs.data ?? []).map((lob) => (
                <option key={lob.id} value={lob.id} style={OPTION_STYLE}>{lob.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="text-xs text-navy-400 block mb-1">Lens</span>
            <select
              className={INPUT_CLASS}
              value={input.lens}
              onChange={(event) => setInput({ ...input, lens: event.target.value as GenerateUseCasesInput['lens'] })}
            >
              <option value="both" style={OPTION_STYLE}>Both</option>
              <option value="ready" style={OPTION_STYLE}>Ready — data already landed</option>
              <option value="gap" style={OPTION_STYLE}>Gap — justifies new ingestion</option>
            </select>
          </label>
          <label>
            <span className="text-xs text-navy-400 block mb-1">How many</span>
            <input
              className={INPUT_CLASS}
              type="number"
              min={1}
              max={15}
              value={input.count}
              onChange={(event) => setInput({ ...input, count: Math.min(15, Math.max(1, Number(event.target.value) || 1)) })}
            />
          </label>
          <label>
            <span className="text-xs text-navy-400 block mb-1">Bias</span>
            <select
              className={INPUT_CLASS}
              value={input.time_horizon_bias ?? ''}
              onChange={(event) => setInput({ ...input, time_horizon_bias: (event.target.value || null) as GenerateUseCasesInput['time_horizon_bias'] })}
            >
              <option value="" style={OPTION_STYLE}>None</option>
              <option value="quick_win" style={OPTION_STYLE}>Quick wins</option>
              <option value="strategic" style={OPTION_STYLE}>Strategic</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-navy-300">
            <input
              type="checkbox"
              checked={input.prioritize_regulatory}
              onChange={(event) => setInput({ ...input, prioritize_regulatory: event.target.checked })}
            />
            Prioritize regulatory / compliance
          </label>
          <button className="btn-primary text-sm ml-auto" onClick={() => setApproveGeneration(true)}>
            Generate
          </button>
        </div>
      </div>

      {approveGeneration ? (
        <ConfirmCard
          data={generationCard}
          approveLabel={generate.isPending ? 'Generating…' : 'Generate candidates'}
          onApprove={() => generate.mutateAsync()}
          onCancel={() => setApproveGeneration(false)}
        />
      ) : null}

      {summary ? (
        <Banner kind="info">
          {summary.total} candidate(s): {summary.ready} ready, {summary.gap} gap. Generated by{' '}
          {preview?.model}. Nothing is saved yet.
        </Banner>
      ) : null}

      {preview?.warnings?.length ? (
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-2">Adjustments made</h3>
          <ul className="list-disc ml-5 text-sm text-navy-400 space-y-1">
            {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      ) : null}

      {candidates.map((candidate) => (
        <div className="card" key={candidate.candidate_id}>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="checkbox"
              aria-label={`Select ${candidate.title}`}
              checked={selected.has(candidate.candidate_id)}
              onChange={(event) => {
                const next = new Set(selected)
                if (event.target.checked) next.add(candidate.candidate_id)
                else next.delete(candidate.candidate_id)
                setSelected(next)
              }}
            />
            <strong className="text-white grow">{candidate.title}</strong>
            <span className={candidate.lens === 'ready' ? 'badge-success' : 'badge-warning'}>{candidate.lens}</span>
            <span className={candidate.effort_tshirt === 'S' ? 'badge-success' : 'badge-warning'}>{candidate.effort_tshirt}</span>
            {candidate.is_regulatory ? <span className="badge-danger">regulatory</span> : null}
          </div>
          <p className="text-sm text-navy-200 mt-2">{candidate.description}</p>
          <p className="text-sm text-navy-400 mt-1">{candidate.business_value}</p>
          <div className="flex flex-wrap items-center gap-1.5 mt-2 text-xs text-navy-400">
            <strong>Needs:</strong>
            {candidate.required_domains.map((domain) => (
              <span key={domain.name ?? domain.label} className={domain.satisfied ? 'badge-success' : 'badge-danger'}>
                {domain.label}
              </span>
            ))}
          </div>
        </div>
      ))}

      {preview ? (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-navy-300">
            <input type="checkbox" checked={inPortfolio} onChange={(event) => setInPortfolio(event.target.checked)} />
            Add to the active portfolio
          </label>
          <button
            className="btn-primary text-sm ml-auto"
            disabled={!selected.size || prepare.isPending}
            onClick={() => prepare.mutate()}
          >
            {prepare.isPending ? 'Preparing…' : 'Review & approve selected'}
          </button>
        </div>
      ) : null}

      {confirm ? (
        <ConfirmCard
          token={confirm.token}
          data={confirm}
          approveLabel={`Create ${selected.size} use case${selected.size === 1 ? '' : 's'}`}
          onApplied={invalidatePortfolio}
          onCancel={() => setConfirm(null)}
        />
      ) : null}
    </div>
  )
}
