import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileText } from 'lucide-react'

import { api } from '../api'
import { Banner } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { Markdown } from '../components/Markdown'
import { useApiErrorToast } from '../components/Toasts'
import { isApiError } from '../lib/errors'
import { NO_RETRY } from '../lib/retry'
import type { ConfirmCardData, ProposalContextResponse, ProposalGenerateResponse } from '../types'

export function proposalGenerationConfirmation(useCaseId: number): ConfirmCardData {
  return {
    token: 'generate-proposal',
    intent: 'generate_proposal',
    summary: `Generate an eight-section proposal for use case ${useCaseId}. This spends an LLM generation, but writes nothing until the generated document is confirmed.`,
    before: { proposal: 'unchanged' },
    after: { proposal: 'preview generated' },
  }
}

export default function ProposalsView({ initialUseCaseId = null }: { initialUseCaseId?: number | null }) {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [idText, setIdText] = useState(initialUseCaseId ? String(initialUseCaseId) : '')
  const [context, setContext] = useState<ProposalContextResponse | null>(null)
  const [proposal, setProposal] = useState<ProposalGenerateResponse | null>(null)
  const [approveGeneration, setApproveGeneration] = useState(false)
  const [regenerate, setRegenerate] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)

  const useCaseId = Number.parseInt(idText, 10)
  const validId = Number.isFinite(useCaseId) && useCaseId > 0

  const contextMutation = useMutation({
    mutationFn: () => api.proposalContext(useCaseId),
    ...NO_RETRY,
    onSuccess: (response) => {
      setContext(response)
      setProposal(null)
      setConflict(null)
    },
    onError: (error) => reportError(error, 'Could not load the proposal context.'),
  })

  const generate = useMutation({
    mutationFn: () => api.generateProposal(useCaseId, regenerate),
    ...NO_RETRY,
    onSuccess: (response) => {
      setProposal(response)
      setConflict(null)
      setApproveGeneration(false)
      setRegenerate(false)
    },
    onError: (error) => {
      if (isApiError(error) && error.isConflict && /already exists/i.test(error.message)) {
        setConflict(error.message)
        setApproveGeneration(false)
        return
      }
      throw error
    },
  })

  useEffect(() => {
    if (!initialUseCaseId) return
    setIdText(String(initialUseCaseId))
    setTimeout(() => contextMutation.mutate(), 0)
    // The drawer prefill is intentionally one-shot; later edits belong to the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialUseCaseId])

  const applied = (response: Record<string, unknown>) => {
    queryClient.invalidateQueries({ queryKey: ['kb-articles'] })
    queryClient.invalidateQueries({ queryKey: ['kb-tree'] })
    setProposal(null)
    const slug = typeof response.slug === 'string' ? response.slug : 'proposal'
    const version = typeof response.version === 'number' ? ` v${response.version}` : ''
    setConflict(`Saved as ${slug}${version}${response.replaced ? ' — the previous text remains in article history.' : ''}`)
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <FileText className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Use-case proposals</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[82ch]">
          An eight-section written proposal grounded in this instance’s researched company profile,
          computed value and assumptions, and real data gaps. It is filed in the knowledge base and
          attached to the use case only after confirmation.
        </p>
      </div>

      <div className="card space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <label>
            <span className="text-xs text-navy-400 block mb-1">Use case id</span>
            <input
              className="bg-navy-700 border border-navy-600 rounded text-sm px-2.5 py-1.5 text-navy-300 w-36"
              type="number"
              min={1}
              value={idText}
              onChange={(event) => {
                setIdText(event.target.value)
                setContext(null)
                setProposal(null)
                setConflict(null)
              }}
              placeholder="Use case id"
            />
          </label>
          <button className="btn-secondary text-sm" disabled={!validId || contextMutation.isPending} onClick={() => contextMutation.mutate()}>
            {contextMutation.isPending ? 'Reading…' : 'Check what it knows'}
          </button>
          <button className="btn-primary text-sm" disabled={!validId} onClick={() => { setRegenerate(false); setApproveGeneration(true) }}>
            Generate proposal
          </button>
        </div>
        <p className="text-xs text-navy-500">
          “Check what it knows” makes no model call. It shows exactly what the agent would be told,
          so a missing value model can be fixed before spending a generation.
        </p>
      </div>

      {approveGeneration && validId ? (
        <ConfirmCard
          data={proposalGenerationConfirmation(useCaseId)}
          approveLabel={generate.isPending ? 'Writing…' : regenerate ? 'Regenerate proposal' : 'Generate proposal'}
          onApprove={() => generate.mutateAsync()}
          onCancel={() => { setApproveGeneration(false); setRegenerate(false) }}
        />
      ) : null}

      {conflict ? (
        <Banner kind={conflict.startsWith('Saved as') ? 'ok' : 'warn'}>
          <div className="space-y-2">
            <p>{conflict}</p>
            {!conflict.startsWith('Saved as') ? (
              <button className="btn-primary text-xs" onClick={() => { setRegenerate(true); setApproveGeneration(true) }}>
                Regenerate anyway
              </button>
            ) : null}
          </div>
        </Banner>
      ) : null}

      {context ? (
        <div className="space-y-3">
          {(context.warnings ?? []).map((warning) => <Banner key={warning} kind="warn">{warning}</Banner>)}
          <div className="card">
            <h3 className="font-semibold text-white">{context.use_case.title}</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
              <Metric label="Annual value" value={context.context.value?.mid != null ? `$${context.context.value.mid}M` : '—'} />
              <Metric label="Domains met" value={(context.context.domains?.satisfied ?? []).length} />
              <Metric label="Data gaps" value={(context.context.domains?.gaps ?? []).length} />
              <Metric label="Sources" value={(context.context.sources ?? []).length} />
            </div>
            <p className="text-xs text-navy-500 mt-4 mb-1">Everything the agent would be told:</p>
            <pre className="max-h-80 overflow-auto rounded bg-navy-900 p-3 text-xs text-navy-300">{JSON.stringify(context.context, null, 2)}</pre>
          </div>
        </div>
      ) : null}

      {proposal ? (
        <div className="space-y-4">
          {(proposal.warnings ?? []).map((warning) => <Banner key={warning} kind="warn">{warning}</Banner>)}
          <ConfirmCard
            token={proposal.confirm.token}
            data={proposal.confirm}
            approveLabel="Save proposal"
            onApplied={(response) => applied(response)}
            onCancel={() => setProposal(null)}
          />
          <div className="card max-h-[520px] overflow-auto">
            <h3 className="text-xs uppercase tracking-wide text-navy-500 mb-2">Preview</h3>
            <Markdown md={proposal.preview_md} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border border-navy-700 bg-navy-900/40 p-3">
      <div className="text-xs text-navy-500">{label}</div>
      <div className="text-lg font-semibold text-white mt-0.5">{value}</div>
    </div>
  )
}
