// Day-one population: the two ways to get a real portfolio without typing it in.
//
// Both cards are deliberately preview-then-apply. Populating touches the whole
// model — ingestion status, use-case status and the value assumptions — so a
// blind write would leave a workshop unable to say what changed. The Excel path
// shows a diff before it commits; the Databricks path asks for consent before it
// reads anything, because the queries run against the customer's own workspace.

import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CircleCheck,
  Cloud,
  Download,
  FileSpreadsheet,
  ShieldCheck,
  TriangleAlert,
  Upload,
} from 'lucide-react'

import { api } from '../api'
import { useApiErrorToast } from '../components/Toasts'
import { accountHeaders } from '../lib/account'
import { describeError } from '../lib/errors'
import type { OnboardingImportResponse } from '../types'

/**
 * A single diffed row. Data-source rows carry `label`, use-case rows carry
 * `title`, and both carry the row id the write will target — which is also the
 * only stable React key here. `OnboardingImportResponse` models neither, so the
 * extra fields are narrowed locally rather than widened for every caller.
 */
interface ImportChangeRow {
  id: number
  label?: string
  title?: string
  from?: string | number
  to?: string | number
}

interface ImportPreview extends Omit<OnboardingImportResponse, 'changes'> {
  changes?: {
    data_sources?: ImportChangeRow[]
    use_cases?: ImportChangeRow[]
    assumptions?: ImportChangeRow[]
  }
}

/** `applied` is tracked next to the payload because the apply response omits the
 *  `summary` the preview used to decide whether there was anything to confirm. */
interface ImportResult {
  preview: ImportPreview
  applied: boolean
}

/**
 * `/live/sync` reports its per-table reachability probe as `available` (not the
 * `system_tables` key `/live/status` uses) and carries the row id on each advanced
 * source. `LiveSyncResponse` models neither, and this is the only view that reads
 * both, so the shape is narrowed here through the response's index signature.
 */
interface DetectionResult {
  available?: Record<string, boolean>
  asset_changes?: { id?: number; label: string }[]
  uc_changes?: { asset: string }[]
  notes?: string[]
}

const STEPS = [
  'Browse the Use Case Catalog and add the ideas that fit — or add your own — to build your Portfolio.',
  'Mark the data sources you already have (landed / curated / governed) in Data Assets.',
  'Watch your portfolio use cases become "shovel-ready" as their required data lands.',
  'Advance a use case (scoping → in-progress → live) as you deliver it.',
  'See what each delivery unlocks on the Value Flywheel, then build your dependency-respecting roadmap and track realized value.',
]

const ONBOARDING_TEMPLATE_URL = '/api/onboarding/export.xlsx'
const DEFAULT_TEMPLATE_FILENAME = 'onboarding-template.xlsx'

function templateFilename(contentDisposition: string | null): string {
  if (!contentDisposition) return DEFAULT_TEMPLATE_FILENAME

  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim())
    } catch {
      return encoded.trim()
    }
  }

  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1]?.trim() || DEFAULT_TEMPLATE_FILENAME
}

export async function downloadOnboardingTemplate(): Promise<void> {
  let response: Response
  try {
    response = await fetch(ONBOARDING_TEMPLATE_URL, { headers: accountHeaders() })
  } catch (cause) {
    throw describeError({ cause })
  }

  if (!response.ok) {
    let body: unknown
    try {
      body = response.headers.get('content-type')?.includes('application/json')
        ? await response.json()
        : await response.text()
    } catch {
      body = null
    }
    throw describeError({
      status: response.status,
      body,
      header: (name) => response.headers.get(name),
    })
  }

  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = templateFilename(response.headers.get('content-disposition'))
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
  }
}

function GettingStarted() {
  return (
    <div className="card border-l-4 border-l-lava">
      <h2 className="font-bold text-white mb-1">Getting started</h2>
      <p className="text-sm text-navy-400 mb-3">
        Two ways to populate your portfolio from the reference library: fill a spreadsheet offline,
        or auto-detect from your Databricks workspace. Then the flywheel comes to life.
      </p>
      <ol className="grid grid-cols-1 md:grid-cols-5 gap-2 text-xs">
        {STEPS.map((step, index) => (
          <li key={index} className="bg-navy-700/50 rounded p-2 border border-navy-600">
            <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-lava text-white font-bold mb-1">
              {index + 1}
            </span>
            <div className="text-navy-300">{step}</div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function ExcelCard() {
  const queryClient = useQueryClient()
  const toastError = useApiErrorToast()
  const fileInput = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [pending, setPending] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const downloadTemplate = async () => {
    setDownloading(true)
    try {
      await downloadOnboardingTemplate()
    } catch (error) {
      toastError(error, 'Could not download the onboarding template.')
    } finally {
      setDownloading(false)
    }
  }

  // Routed through the shared axios instance rather than a raw `fetch`, so the
  // account interceptor scopes it like every other request — a multipart upload
  // that lands in the DEFAULT account while the rest of the app reads the
  // selected one is the exact failure §4.1 of the migration plan warns about.
  // axios sets the multipart boundary itself when handed a FormData, so there
  // are no JSON defaults to undo. Applying rewrites sources, use cases and
  // assumptions at once, so every cache that reads any of them — including the
  // two derived totals — has to go.
  const send = async (file: File, apply: boolean) => {
    setBusy(true)
    try {
      const body = new FormData()
      body.append('file', file)
      const preview = await api.importOnboarding<ImportPreview>(body, apply)
      if (apply) {
        queryClient.invalidateQueries({ queryKey: ['data-assets'] })
        queryClient.invalidateQueries({ queryKey: ['use-cases'] })
        queryClient.invalidateQueries({ queryKey: ['assumptions'] })
        queryClient.invalidateQueries({ queryKey: ['dashboard'] })
        queryClient.invalidateQueries({ queryKey: ['portfolio-value'] })
      }
      setResult({ preview, applied: apply })
    } finally {
      setBusy(false)
    }
  }

  const preview = result?.preview
  const errors = preview?.errors ?? []
  const dataSources = preview?.changes?.data_sources ?? []
  const useCases = preview?.changes?.use_cases ?? []
  const assumptions = preview?.changes?.assumptions ?? []
  const summary = preview?.summary
  const summaryTotal =
    (summary?.data_sources ?? 0) + (summary?.use_cases ?? 0) + (summary?.assumptions ?? 0)

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-2">
        <FileSpreadsheet className="w-5 h-5 text-success" />
        <h3 className="font-semibold text-white">Bulk populate via Excel</h3>
      </div>
      <p className="text-sm text-navy-400 mb-3">
        Download a workbook pre-filled with the reference library. Circulate it, have LOB owners mark
        their data sources, project statuses, and value inputs, then upload to preview &amp; apply.
      </p>

      <div className="flex gap-2">
        <button
          className="btn-secondary text-sm"
          disabled={downloading}
          aria-busy={downloading}
          onClick={() => void downloadTemplate()}
        >
          <Download className="w-4 h-4" /> Download template
        </button>
        <button
          className="btn-primary text-sm"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          <Upload className="w-4 h-4" /> Upload filled file
        </button>
        <input
          id="onboarding-file"
          name="onboarding-file"
          aria-label="Upload filled onboarding workbook"
          ref={fileInput}
          type="file"
          accept=".xlsx,.csv"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (!file) return
            // Kept for the confirm step: the input is cleared by the browser as
            // soon as the dialog closes, so the apply call needs its own handle.
            setPending(file)
            send(file, false)
          }}
        />
      </div>

      {preview ? (
        <div className="mt-3 border border-navy-600 rounded p-3">
          {errors.length > 0 ? (
            <div className="text-xs text-warning mb-2 flex items-start gap-1">
              <TriangleAlert className="w-3.5 h-3.5 mt-0.5" />
              <div>
                {errors.length} row error(s): {errors.slice(0, 3).join('; ')}
              </div>
            </div>
          ) : null}

          <div className="text-sm text-white font-medium mb-1">
            {result?.applied ? `Applied ${preview.applied ?? 0} change(s)` : 'Preview of changes'}
          </div>

          <div className="text-xs text-navy-300 space-y-0.5 max-h-40 overflow-y-auto">
            <div>
              {dataSources.length} data sources · {useCases.length} use cases ·{' '}
              {assumptions.length} assumptions
            </div>
            {dataSources.slice(0, 6).map((change) => (
              <div key={`d${change.id}`}>
                · {change.label}: {change.from} → <span className="text-success">{change.to}</span>
              </div>
            ))}
            {useCases.slice(0, 6).map((change) => (
              <div key={`u${change.id}`}>
                · {change.title}: {change.from} → <span className="text-success">{change.to}</span>
              </div>
            ))}
          </div>

          {!result?.applied && pending && summaryTotal > 0 ? (
            <button
              className="btn-primary text-sm mt-2"
              disabled={busy}
              onClick={() => send(pending, true)}
            >
              <CircleCheck className="w-4 h-4" /> Confirm &amp; apply
            </button>
          ) : null}

          {!result?.applied && summaryTotal === 0 ? (
            <div className="text-xs text-navy-500 mt-1">
              No changes detected in the uploaded file.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function AutoPopulateCard() {
  const queryClient = useQueryClient()
  const [askingConsent, setAskingConsent] = useState(false)
  const [consented, setConsented] = useState(false)
  const [detection, setDetection] = useState<DetectionResult | null>(null)

  const detect = useMutation({
    mutationFn: (): Promise<DetectionResult> => api.liveSync(true),
    onSuccess: (result) => {
      setDetection(result)
      setAskingConsent(false)
      queryClient.invalidateQueries({ queryKey: ['data-assets'] })
      queryClient.invalidateQueries({ queryKey: ['use-cases'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  const assetChanges = detection?.asset_changes ?? []
  const ucChanges = detection?.uc_changes ?? []

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-2">
        <Cloud className="w-5 h-5 text-info" />
        <h3 className="font-semibold text-white">Auto-populate from Databricks</h3>
      </div>
      <p className="text-sm text-navy-400 mb-3">
        Detect which data sources are already landed and which use cases are live, directly from your
        workspace's system tables — read-only, and only with your explicit consent.
      </p>
      <button className="btn-primary text-sm" onClick={() => setAskingConsent(true)}>
        <Cloud className="w-4 h-4" /> Populate from Databricks (auto)
      </button>

      {askingConsent ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setAskingConsent(false)} />
          <div className="relative card w-full max-w-lg animate-scale-in">
            <div className="flex items-center gap-2 mb-2">
              <ShieldCheck className="w-5 h-5 text-info" />
              <h3 className="font-bold text-lg">Permission required</h3>
            </div>
            <p className="text-sm text-navy-300 mb-2">
              This will run <strong>read-only</strong> queries against your Databricks system tables
              and SQL warehouse to detect real usage:
            </p>
            <ul className="text-xs text-navy-400 list-disc pl-5 mb-3 space-y-0.5">
              <li>
                <code>system.access.table_lineage</code> — detect which registered data sources
                actually have data landed
              </li>
              <li>
                <code>system.lakeflow.*</code> — detect jobs/pipelines that are running (→ use case
                is live)
              </li>
              <li>
                <code>system.serving.*</code> — detect active model endpoints
              </li>
              <li>
                <code>system.billing.usage</code> — detect active consumption
              </li>
            </ul>
            <p className="text-xs text-navy-400 mb-3">
              It will then advance ingestion status for detected landed sources and mark use cases
              live where consumption exists. Nothing is deleted; every change is shown and audited.
              If system tables aren't accessible in this workspace, it will report that and make no
              changes.
            </p>
            <label className="flex items-center gap-2 text-sm text-navy-200 mb-3 cursor-pointer">
              <input
                id="onboarding-consent"
                name="onboarding-consent"
                aria-label="Authorize read-only queries against my workspace"
                type="checkbox"
                checked={consented}
                onChange={(event) => setConsented(event.target.checked)}
              />
              I authorize these read-only queries against my workspace.
            </label>
            <div className="flex justify-end gap-2">
              <button className="btn-secondary text-sm" onClick={() => setAskingConsent(false)}>
                Cancel
              </button>
              <button
                className="btn-primary text-sm"
                disabled={!consented || detect.isPending}
                onClick={() => detect.mutate()}
              >
                {detect.isPending ? 'Scanning…' : 'Run detection'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {detection ? (
        <div className="mt-3 border border-navy-600 rounded p-3">
          <div className="text-xs text-navy-400 mb-1">
            System tables:{' '}
            {Object.entries(detection.available ?? {}).map(([table, reachable]) => (
              <span key={table} className={`badge-${reachable ? 'low' : 'critical'} ml-1`}>
                {table}
              </span>
            ))}
          </div>
          <div className="text-sm text-white font-medium">
            Detected: {assetChanges.length} landed sources, {ucChanges.length} live use cases
          </div>
          <div className="text-xs text-navy-300 max-h-40 overflow-y-auto mt-1">
            {assetChanges.slice(0, 8).map((change) => (
              <div key={change.id}>
                · {change.label}: → <span className="text-success">landed</span>
              </div>
            ))}
            {(detection.notes ?? []).map((note, index) => (
              <div key={index} className="text-navy-500">
                {note}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function OnboardingView() {
  return (
    <div className="space-y-4">
      <GettingStarted />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ExcelCard />
        <AutoPopulateCard />
      </div>
    </div>
  )
}
