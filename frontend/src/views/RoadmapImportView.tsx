import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Import } from 'lucide-react'

import { api } from '../api'
import { Banner } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { FileDrop } from '../components/FileDrop'
import { useApiErrorToast } from '../components/Toasts'
import { NO_RETRY } from '../lib/retry'
import type {
  ConfirmCardData,
  RoadmapImportApplyResponse,
  RoadmapImportPreviewResponse,
  RoadmapPackage,
} from '../types'

const PLACEHOLDER = '{"schemaVersion":"grid-atlas.sync.v1","packageType":"maturity_assessment_roadmap","useCases":[]}'

export function parseRoadmapPackage(raw: string): RoadmapPackage {
  if (!raw.trim()) throw new Error('Choose a roadmap package JSON file or paste JSON.')
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`Package is not valid JSON: ${error instanceof Error ? error.message : String(error)}`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Package must be a JSON object.')
  }
  const envelope = parsed as Record<string, unknown>
  const roadmapPackage = envelope.package && typeof envelope.package === 'object' && !Array.isArray(envelope.package)
    ? envelope.package as RoadmapPackage
    : envelope as RoadmapPackage
  const useCases = roadmapPackage.useCases ?? roadmapPackage.use_cases ?? []
  if (!Array.isArray(useCases) || useCases.length === 0) {
    throw new Error('Package must include a non-empty useCases array.')
  }
  return roadmapPackage
}

export function roadmapImportConfirmation(preview: RoadmapImportPreviewResponse): ConfirmCardData {
  const useCases = preview.useCases ?? {}
  return {
    token: 'roadmap-import',
    intent: 'apply_maturity_roadmap',
    summary: 'Apply this roadmap package to the operational portfolio.',
    before: { portfolio_use_cases: 'unchanged' },
    after: {
      incoming: useCases.incoming ?? 0,
      mapped: useCases.mapped ?? 0,
      create_or_match: useCases.toCreateOrTitleMatch ?? 0,
    },
  }
}

export default function RoadmapImportView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [raw, setRaw] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [preview, setPreview] = useState<RoadmapImportPreviewResponse | null>(null)
  const [applied, setApplied] = useState<RoadmapImportApplyResponse | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [approveImport, setApproveImport] = useState(false)

  const roadmapPackage = useMemo(() => {
    try {
      return parseRoadmapPackage(raw)
    } catch {
      return null
    }
  }, [raw])

  const previewImport = useMutation({
    mutationFn: () => api.previewRoadmapImport(parseRoadmapPackage(raw)),
    ...NO_RETRY,
    onSuccess: (response) => {
      setPreview(response)
      setApplied(null)
      setApproveImport(false)
      setFormError(null)
    },
    onError: (error) => reportError(error, 'Could not preview the roadmap package.'),
  })

  const applyImport = useMutation({
    mutationFn: () => api.applyRoadmapImport(parseRoadmapPackage(raw)),
    ...NO_RETRY,
    onSuccess: (response) => {
      setApplied(response)
      setApproveImport(false)
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
      queryClient.invalidateQueries({ queryKey: ['coverage-matrix'] })
      queryClient.invalidateQueries({ queryKey: ['roadmap-items'] })
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
    },
  })

  const read = (file: File) => {
    void file.text().then((text) => {
      setRaw(text)
      setFileName(file.name)
      setPreview(null)
      setApplied(null)
      setApproveImport(false)
      setFormError(null)
    })
  }

  const validateAndPreview = () => {
    try {
      parseRoadmapPackage(raw)
      setFormError(null)
      previewImport.mutate()
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Import className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Roadmap import</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[82ch]">
          Bring a generated maturity-assessment roadmap into this operational portfolio. Preview
          maps incoming use cases against existing Value Flywheel records; apply writes only after
          that preview has been reviewed and confirmed.
        </p>
      </div>

      {fileName ? <Banner kind="info">{fileName} loaded. Preview before applying.</Banner> : null}
      {formError ? <Banner kind="err">{formError}</Banner> : null}
      {preview ? <ImportResult result={preview} applied={false} /> : null}
      {applied ? <ImportResult result={applied} applied /> : null}

      <div className="card space-y-4">
        <h3 className="text-sm font-semibold text-white">Roadmap package</h3>
        <FileDrop
          onFile={read}
          accept=".json,application/json"
          validate={(file) => file.size === 0 ? `${file.name} is empty.` : file.name.toLowerCase().endsWith('.json') ? null : 'Choose a JSON package.'}
          label="Drop a roadmap JSON package here, or click to choose"
          hint="JSON exported by the Data & AI Maturity Assessment app."
        />
        <label className="block">
          <span className="text-xs text-navy-400 block mb-1">Or paste package JSON</span>
          <textarea
            className="w-full min-h-72 bg-navy-900 border border-navy-600 rounded p-3 font-mono text-xs text-navy-200 focus:outline-none focus:border-info"
            value={raw}
            placeholder={PLACEHOLDER}
            onChange={(event) => {
              setRaw(event.target.value)
              setFileName(null)
              setPreview(null)
              setApplied(null)
              setApproveImport(false)
              setFormError(null)
            }}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary text-sm" disabled={previewImport.isPending} onClick={validateAndPreview}>
            {previewImport.isPending ? 'Previewing…' : 'Preview import'}
          </button>
          <button
            className="btn-primary text-sm"
            disabled={!preview || !roadmapPackage}
            title={!preview ? 'Preview the current package before applying it.' : undefined}
            onClick={() => setApproveImport(true)}
          >
            Apply import
          </button>
        </div>
      </div>

      {approveImport && preview ? (
        <ConfirmCard
          data={roadmapImportConfirmation(preview)}
          approveLabel={applyImport.isPending ? 'Importing…' : 'Apply roadmap import'}
          onApprove={() => applyImport.mutateAsync()}
          onCancel={() => setApproveImport(false)}
        />
      ) : null}

      <div className="card">
        <h3 className="text-sm font-semibold text-white">Expected source</h3>
        <p className="text-sm text-navy-400 mt-1">
          Export endpoint: <code className="text-lava-300">/api/exports/value-flywheel-roadmap?assessmentId=&lt;id&gt;</code>{' '}
          from the Data &amp; AI Maturity Assessment app.
        </p>
      </div>
    </div>
  )
}

function ImportResult({ result, applied }: { result: RoadmapImportPreviewResponse | RoadmapImportApplyResponse; applied: boolean }) {
  const preview = result as RoadmapImportPreviewResponse
  const apply = result as RoadmapImportApplyResponse
  const useCases = preview.useCases ?? {}
  const summary = apply.summary ?? {}
  const idMap = apply.idMap ?? {}
  const incoming = useCases.incoming ?? Object.keys(idMap).length
  const mapped = useCases.mapped ?? summary.mapped ?? 0
  const createOrMatch = useCases.toCreateOrTitleMatch ?? ((summary.created ?? 0) + (summary.updated ?? 0))

  return (
    <div className="space-y-3">
      <Banner kind={applied ? 'ok' : 'info'}>
        {applied ? 'Roadmap package imported.' : 'Preview complete. Nothing has been written.'}
      </Banner>
      <div className="card">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Metric label="Incoming" value={incoming} />
          <Metric label="Mapped" value={mapped} />
          <Metric label="Create / match" value={createOrMatch} />
          <Metric label="Roadmap items" value={preview.roadmapItems ?? 0} />
        </div>
        {applied ? (
          <table className="w-full text-sm mt-4">
            <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
              <tr><th className="text-left py-2">Action</th><th className="text-right py-2">Count</th></tr>
            </thead>
            <tbody>
              {Object.entries(summary).map(([action, count]) => (
                <tr key={action} className="border-b border-navy-700">
                  <td className="py-2 text-navy-300">{action}</td><td className="py-2 text-right text-white">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <p className="text-xs text-navy-500 mt-3">Source app: {result.sourceApp ?? 'data-ai-maturity-assessment'}</p>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div><div className="text-xs text-navy-500">{label}</div><div className="text-xl font-semibold text-white">{value}</div></div>
}
