// Get started — the SINGLE onboarding surface.
//
// Tier 3 Phase 9 (TIER3_MIGRATION_PLAN.md §3.1, §4.3). This MERGES three
// overlapping surfaces into one, rather than porting three:
//
//   - the SPA's own `OnboardingView` (the xlsx round-trip and the
//     detect-from-Databricks card, both preserved below as step 2B and 2C);
//   - `console.js viewStart` (the 5-step dependency-ordered wizard, 381 lines);
//   - the console's `#setup` / `#discovery` aliases, which already pointed at
//     `viewStart` (`console.js:3724-3725`) and are folded in here with it.
//
// All three answered "how do I get my data in?", so a user had to know which one
// to open — and the SPA's tab silently duplicated work the console's pipeline does
// better. The console's wizard is a strict SUPERSET of the SPA's tab, so the merge
// takes the wizard's shape and keeps the SPA's two cards as its step 2. Two of the
// three surfaces are RETIRED, not reproduced; that deletion is the point.
//
// WHY IT IS A DEPENDENCY-ORDERED CHECKLIST AND NOT A TAB SET
// ---------------------------------------------------------
// Every step here depends on the one before it: you cannot enrich an inventory you
// have not uploaded, and you cannot attribute tables you have not enriched. So the
// probes run first and the steps LOCK on their results (`hasInventory`,
// `hasEnrichment`) rather than failing when clicked. A locked step says what to do
// to unlock it, which is the difference between a checklist and a maze.
//
// WHAT THIS DELIBERATELY DOES NOT DO
// ----------------------------------
//   - It does not use `<ConfirmCard>`. That primitive consumes a single-use
//     server-issued token, and NONE of these routes issues one — `?apply=false`
//     on `/onboarding/import` and `/live/sync` is the route's own preview flag,
//     computed fresh on the apply call. Wrapping it in a confirm card would imply
//     a server-side gate that does not exist.
//   - It does not gate `advance_status` behind a confirmation. The server caps it
//     at `landed` and only ever moves a status FORWARD
//     (`ingestion.py:836-846`), so it cannot demote a source a human marked
//     governed. The honest UI is to say what it changes, in words, next to the
//     checkbox.
//   - It does not download through an `<a href="/api/…">`. Every request here goes
//     through the shared axios client so the account interceptor scopes it; the
//     two blob downloads use `saveBlob`. §4.1: a missing account header does not
//     raise — the server falls back to the default account — so an anchor is a
//     silent tenant leak, and the workbook export leaks a whole portfolio.
//
// Endpoints: GET /setup/status, GET /setup/grants, GET /ingestion/summary,
// GET /ingestion/extractor/{info,download}, POST /ingestion/bootstrap,
// POST /ingestion/upload/{schemas,tables,columns}, POST /ingestion/enrich/{schemas,tables},
// POST /ingestion/canonicalize, POST /ingestion/attribute,
// GET /onboarding/export.xlsx, POST /onboarding/import?apply=, POST /live/sync?apply=.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import {
  Check,
  CircleCheck,
  Cloud,
  Download,
  FileSpreadsheet,
  Rocket,
  ShieldCheck,
  Sparkles,
  Wrench,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { api } from '../api'
import { Banner } from '../components/Banner'
import { FileDrop, rejectionOf } from '../components/FileDrop'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import type { TabId } from '../components/Header'
import { saveBlob } from '../lib/download'
import { NO_RETRY } from '../lib/retry'
import type {
  IngestionSummaryResponse,
  OnboardingImportResponse,
  SetupCheck,
  SetupStatusResponse,
} from '../types'

/**
 * The discovery CSVs' server-side cap — `server/routes/ingestion.py:53`,
 * `MAX_UPLOAD_BYTES = 64 * 1024 * 1024`.
 *
 * Four times the KB attachment cap, and it has to be: a 200k-table estate
 * extracts to roughly 40 MB of `all_columns.csv`. `<FileDrop>` defaults to the
 * attachment rules, so passing this is what stops the drop zone rejecting a valid
 * extract before the request is even made.
 */
const MAX_INVENTORY_BYTES = 64 * 1024 * 1024

/** The extractor writes CSV. `.csv` only, so a mis-picked workbook is caught here. */
const INVENTORY_EXTENSIONS = ['.csv'] as const

/** The workbook round-trip accepts either, exactly as `_parse_workbook` does. */
const WORKBOOK_EXTENSIONS = ['.xlsx', '.csv'] as const

const DEFAULT_TEMPLATE_FILENAME = 'onboarding-template.xlsx'
const EXTRACTOR_FILENAME = 'grid-atlas-schema-extractor.zip'

/** `1,204`, or an em dash for nothing — the console's `num()` (`console.js:56-60`). */
function num(value?: number | null): string {
  if (value == null) return '—'
  return Number.isFinite(value) ? value.toLocaleString() : '—'
}

/**
 * The filename the server chose, from its `Content-Disposition`.
 *
 * Exported for the unit test. The server names these per account
 * (`onboarding.py:160`), so inventing a client-side name would discard the one
 * piece of the response that says which portfolio the bytes came from. RFC 5987's
 * `filename*=UTF-8''…` is preferred when present because it is the form that
 * survives a non-ASCII company name.
 */
export function templateFilename(contentDisposition: string | null): string {
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

/**
 * Fetch the onboarding workbook and save it to disk.
 *
 * Exported so `frontend/test/plumbing.test.ts` can pin the claim that matters:
 * this request carries the account header. It goes through `api.onboardingTemplate()`
 * — the shared axios instance — so the header comes from the interceptor rather
 * than from this call site remembering to add it, which is the whole argument of
 * §4.1. The previous version used a raw `fetch` plus `accountHeaders()` and its own
 * anchor-click dance; both halves are now the shared ones.
 */
export async function downloadOnboardingTemplate(): Promise<void> {
  const { blob, contentDisposition } = await api.onboardingTemplate()
  saveBlob(blob, templateFilename(contentDisposition))
}

// ---------------------------------------------------------------------------
// Layout primitives local to the wizard
// ---------------------------------------------------------------------------

type StepState = 'done' | 'active' | 'idle'

/** The console's `.step-num` (`stepNum()`), as Tailwind. A tick when done. */
function StepNumber({ n, state }: { n: number; state: StepState }) {
  const styles: Record<StepState, string> = {
    done: 'bg-success text-white',
    active: 'bg-lava text-white',
    idle: 'bg-navy-700 text-navy-400 border border-navy-600',
  }
  return (
    <div
      aria-hidden
      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${styles[state]}`}
    >
      {state === 'done' ? <Check className="h-4 w-4" /> : n}
    </div>
  )
}

/**
 * One numbered step.
 *
 * `locked` dims it and, crucially, says WHY — the console's `.step.locked` only
 * dimmed, which left a greyed-out card with no explanation of what would ungrey
 * it. The children still render when locked (the console replaced them with a
 * one-liner) only where they are safe to look at; the callers below pass the
 * one-liner themselves for the steps whose controls must not be reachable.
 */
function Step({
  n,
  state,
  title,
  why,
  locked = false,
  children,
}: {
  n: number
  state: StepState
  title: string
  why: string
  locked?: boolean
  children: ReactNode
}) {
  return (
    <section className="flex gap-3">
      <StepNumber n={n} state={locked ? 'idle' : state} />
      <div className={`card min-w-0 flex-1 space-y-3 ${locked ? 'opacity-60' : ''}`}>
        <div>
          <h3 className="font-semibold text-white">
            {n} · {title}
          </h3>
          <p className="mt-1 max-w-[80ch] text-sm text-navy-400">{why}</p>
        </div>
        {children}
      </div>
    </section>
  )
}

/** A nested card — the console's `background:var(--navy-900)` inner cards. */
function SubCard({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-3 rounded-lg border border-navy-700 bg-navy-900/60 p-4">{children}</div>
  )
}

/** GRANT SQL, monospaced and selectable. Never `Markdown`: this is SQL to
 *  copy verbatim into a Databricks editor, not prose to render. */
function Sql({ children }: { children: string }) {
  return (
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-navy-700 bg-navy-900 p-3 font-mono text-xs text-navy-200">
      {children}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — the workspace probes
// ---------------------------------------------------------------------------

/** ok / required-and-failing / optional-and-failing, as the console's pills. */
function CheckPill({ check }: { check: SetupCheck }) {
  const badge = check.ok ? 'badge-low' : check.required ? 'badge-critical' : 'badge-medium'
  return (
    <span className={badge} title={check.detail || undefined}>
      {check.label}
    </span>
  )
}

function HealthStep({ status, state }: { status: SetupStatusResponse; state: StepState }) {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [grants, setGrants] = useState<string | null>(null)

  const checks = status.checks ?? []
  const summary = status.summary ?? {}
  const failing = checks.filter((check) => !check.ok)

  // Re-check is an invalidation, not a `setTimeout` refresh: react-query refetches
  // and the render follows the data. The console re-ran the whole view function,
  // which is the vanilla-JS equivalent and the reason its state kept resetting.
  const recheck = useMutation({
    mutationFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ['setup-status'] })
      await queryClient.invalidateQueries({ queryKey: ['ingestion-summary'] })
    },
    onError: (error) => reportError(error, 'Could not re-run the checks.'),
    ...NO_RETRY,
  })

  const showGrants = useMutation({
    mutationFn: api.setupGrants,
    onSuccess: (payload) => setGrants(payload.sql),
    onError: (error) => reportError(error, 'Could not read the GRANT statements.'),
    ...NO_RETRY,
  })

  return (
    <Step
      n={1}
      state={state}
      title="Check the workspace"
      why="Every later step depends on Lakebase, a warehouse, and Unity Catalog. Probing first means a missing grant shows up here with the SQL to fix it, instead of as a 403 halfway through a pipeline."
    >
      {checks.length ? (
        <div className="flex flex-wrap gap-1.5">
          {checks.map((check) => (
            <CheckPill key={check.name} check={check} />
          ))}
        </div>
      ) : null}

      {status.ready ? (
        <Banner kind="ok">
          Ready — {summary.passing ?? 0} of {summary.total ?? 0} checks passing.
          {summary.optional_failing
            ? ` ${summary.optional_failing} optional feature(s) not configured; the portfolio works without them.`
            : ''}
        </Banner>
      ) : (
        <Banner kind="err">
          {summary.required_failing ?? 0} required check(s) failing. {status.next_action ?? ''}
        </Banner>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-secondary text-sm"
          disabled={recheck.isPending}
          aria-busy={recheck.isPending}
          onClick={() => recheck.mutate()}
        >
          {recheck.isPending ? 'Re-checking…' : 'Re-check'}
        </button>
        <button
          className="btn-secondary text-sm"
          disabled={showGrants.isPending}
          aria-busy={showGrants.isPending}
          onClick={() => showGrants.mutate()}
        >
          <Wrench className="h-4 w-4" /> Show all GRANTs
        </button>
        {status.service_principal ? (
          <span className="text-xs text-navy-500">
            Running as <code className="font-mono text-navy-300">{status.service_principal}</code>
          </span>
        ) : null}
      </div>

      {grants ? (
        <SubCard>
          <div>
            <h4 className="text-sm font-semibold text-white">Unity Catalog privileges</h4>
            <p className="mt-1 text-xs text-navy-400">
              Run as a metastore admin or catalog owner. Steps that cannot be expressed as SQL are
              included as comments.
            </p>
          </div>
          <Sql>{grants}</Sql>
        </SubCard>
      ) : null}

      {failing.map((check) => (
        <SubCard key={check.name}>
          <div className="flex items-center justify-between gap-2">
            <strong className="text-sm text-white">{check.label}</strong>
            <span className={check.required ? 'badge-critical' : 'badge-medium'}>
              {check.required ? 'required' : 'optional'}
            </span>
          </div>
          {check.detail ? <p className="text-xs text-navy-400">{check.detail}</p> : null}
          {check.fix ? <p className="text-xs text-navy-200">{check.fix}</p> : null}
          {check.grants?.length ? <Sql>{check.grants.join('\n')}</Sql> : null}
        </SubCard>
      ))}
    </Step>
  )
}

// ---------------------------------------------------------------------------
// Step 2A — sweep the estate
// ---------------------------------------------------------------------------

const INVENTORY_UPLOADS = [
  {
    kind: 'schemas' as const,
    label: 'all_schemas.csv',
    hint: 'One row per Unity Catalog schema. Up to 64 MB.',
  },
  {
    kind: 'tables' as const,
    label: 'all_tables.csv',
    hint: 'One row per table. Up to 64 MB — split by workspace if a single estate exceeds it.',
  },
  {
    kind: 'columns' as const,
    label: 'all_columns.csv (optional — markedly better AI accuracy)',
    hint: 'The largest of the three. Up to 64 MB.',
  },
]

function SweepCard({ inventory }: { inventory: IngestionSummaryResponse }) {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [note, setNote] = useState<string | null>(null)

  const discoveryConfigured = !!inventory.configured
  const discoveryReady = discoveryConfigured && !!inventory.available
  const hasInventory = (inventory.tables ?? 0) > 0

  const extractor = useQuery({ queryKey: ['extractor-info'], queryFn: api.extractorInfo })

  const downloadExtractor = useMutation({
    mutationFn: api.extractorZip,
    onSuccess: (blob) => saveBlob(blob, EXTRACTOR_FILENAME),
    onError: (error) => reportError(error, 'Could not download the extractor.'),
    ...NO_RETRY,
  })

  const bootstrap = useMutation({
    mutationFn: api.ingestionBootstrap,
    onSuccess: (result) => {
      // Invalidated rather than re-read: creating the tables is what flips
      // `available`, and that flip is what reveals the upload controls below.
      queryClient.invalidateQueries({ queryKey: ['ingestion-summary'] })
      queryClient.invalidateQueries({ queryKey: ['setup-status'] })
      setNote(`Discovery tables ready in ${result.catalog}.${result.schema}.`)
    },
    onError: (error) => reportError(error, 'Could not create the discovery tables.'),
    ...NO_RETRY,
  })

  const upload = useMutation({
    mutationFn: ({ kind, file }: { kind: 'schemas' | 'tables' | 'columns'; file: File }) =>
      api.uploadInventoryCsv(kind, file).then((result) => ({ kind, result })),
    onSuccess: ({ kind, result }) => {
      queryClient.invalidateQueries({ queryKey: ['ingestion-summary'] })
      setNote(
        `${kind}: ${num(result.rows_written)} row(s) written from ${num(result.rows_in_file)} in the file.`,
      )
    },
    onError: (error) => reportError(error, 'Could not ingest that CSV.'),
    ...NO_RETRY,
  })

  const info = extractor.data

  return (
    <SubCard>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-white">A · Sweep your Databricks estate</h4>
        <span className={hasInventory ? 'badge-low' : 'badge-medium'}>
          {hasInventory ? `${num(inventory.tables)} tables found` : 'nothing yet'}
        </span>
      </div>
      <p className="text-xs text-navy-400">
        This app can only authenticate to the one workspace it runs in. Download the extractor and
        run it on your own machine — under <em>your</em> credentials, so it reaches every workspace
        you can. Metadata only; no table contents are read, and nothing leaves your machine until
        you upload the CSVs back here.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-secondary text-sm"
          disabled={downloadExtractor.isPending}
          aria-busy={downloadExtractor.isPending}
          onClick={() => downloadExtractor.mutate()}
        >
          <Download className="h-4 w-4" />
          {downloadExtractor.isPending ? 'Preparing…' : 'Download extractor (.zip)'}
        </button>
        {/* Tells the user what they are about to download before they click it. */}
        <span className="text-xs text-navy-500">
          {info
            ? info.available
              ? `${info.files?.length ?? 0} files, ${Math.round((info.total_bytes ?? 0) / 1024)} KB · needs ${info.requires?.[0] ?? 'Python 3.9+'}`
              : 'unavailable in this deployment'
            : ''}
        </span>
      </div>

      {discoveryConfigured ? (
        <>
          {discoveryReady ? (
            <StatStrip
              stats={[
                { label: 'Workspaces', value: num(inventory.workspaces) },
                { label: 'Schemas', value: num(inventory.schemas) },
                { label: 'Tables', value: num(inventory.tables) },
                { label: 'Enriched', value: num(inventory.enriched_tables) },
              ]}
            />
          ) : (
            <Banner kind="warn">
              Discovery storage is configured but not ready:{' '}
              {inventory.error || 'run Create discovery tables, then re-check'}
            </Banner>
          )}

          {/*
            The bootstrap button stays OUTSIDE the `discoveryReady` branch on
            purpose. A first run often has ATLAS_CATALOG set but no discovery
            tables, which reports as configured-but-unavailable — and if this
            button lived in the ready branch, that state would have no way out.
            `tests/test_console_nav.py::TestGetStartedDiscovery` pins the same
            invariant on the console.
          */}
          <button
            className="btn-secondary text-sm"
            disabled={bootstrap.isPending}
            aria-busy={bootstrap.isPending}
            onClick={() => bootstrap.mutate()}
          >
            {bootstrap.isPending ? 'Creating…' : 'Create discovery tables'}
          </button>

          {discoveryReady ? (
            <div className="space-y-3">
              {INVENTORY_UPLOADS.map((slot) => (
                <div key={slot.kind}>
                  <div className="mb-1 text-xs font-medium text-navy-200">{slot.label}</div>
                  <FileDrop
                    onFile={(file) => upload.mutate({ kind: slot.kind, file })}
                    busy={upload.isPending}
                    busyLabel="Ingesting…"
                    label="Drop the CSV here, or click to choose"
                    hint={slot.hint}
                    accept={INVENTORY_EXTENSIONS.join(',')}
                    validate={(file) =>
                      rejectionOf(file, {
                        maxBytes: MAX_INVENTORY_BYTES,
                        extensions: INVENTORY_EXTENSIONS,
                      })
                    }
                  />
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <Banner kind="info">
          Set ATLAS_CATALOG in app.yaml to enable the estate sweep. The curated catalog and the
          workbook below both work without it.
        </Banner>
      )}

      {note ? <Banner kind="ok">{note}</Banner> : null}
    </SubCard>
  )
}

// ---------------------------------------------------------------------------
// Step 2B — the workbook round-trip
// ---------------------------------------------------------------------------

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

/** Applying rewrites sources, use cases and assumptions at once, so every cache
 *  that reads any of them — including the two derived totals — has to go. */
function invalidatePortfolio(queryClient: QueryClient): void {
  for (const key of [
    ['data-assets'],
    ['use-cases'],
    ['assumptions'],
    ['dashboard'],
    ['portfolio-value'],
  ]) {
    queryClient.invalidateQueries({ queryKey: key })
  }
}

function WorkbookCard() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [result, setResult] = useState<ImportResult | null>(null)
  const [pending, setPending] = useState<File | null>(null)

  const downloadTemplate = useMutation({
    mutationFn: downloadOnboardingTemplate,
    onError: (error) => reportError(error, 'Could not download the onboarding template.'),
    ...NO_RETRY,
  })

  // The one multipart upload path in the app, through `http` like everything else
  // so the account interceptor scopes it — a workbook that lands in the DEFAULT
  // account while the rest of the app reads the selected one is exactly the
  // failure §4.1 warns about.
  const send = useMutation({
    mutationFn: ({ file, apply }: { file: File; apply: boolean }) => {
      const body = new FormData()
      body.append('file', file)
      return api.importOnboarding<ImportPreview>(body, apply).then((preview) => ({ preview, apply }))
    },
    onSuccess: ({ preview, apply }) => {
      if (apply) invalidatePortfolio(queryClient)
      setResult({ preview, applied: apply })
    },
    onError: (error) => reportError(error, 'Could not read that workbook.'),
    ...NO_RETRY,
  })

  const preview = result?.preview
  const errors = preview?.errors ?? []
  const dataSources = preview?.changes?.data_sources ?? []
  const useCases = preview?.changes?.use_cases ?? []
  const assumptions = preview?.changes?.assumptions ?? []
  const summary = preview?.summary
  const summaryTotal =
    (summary?.data_sources ?? 0) + (summary?.use_cases ?? 0) + (summary?.assumptions ?? 0)

  return (
    <SubCard>
      <div className="flex items-center gap-2">
        <FileSpreadsheet className="h-4 w-4 text-success" />
        <h4 className="text-sm font-semibold text-white">B · Fill in the workbook</h4>
      </div>
      <p className="text-xs text-navy-400">
        An Excel round-trip over the reference library. Circulate it, have owners mark what is
        actually landed, upload it back. This captures the things discovery can&apos;t infer —
        whether a source is <em>governed</em>, who owns it, what it is worth.
      </p>

      <button
        className="btn-secondary text-sm"
        disabled={downloadTemplate.isPending}
        aria-busy={downloadTemplate.isPending}
        onClick={() => downloadTemplate.mutate()}
      >
        <Download className="h-4 w-4" />
        {downloadTemplate.isPending ? 'Building…' : 'Download workbook'}
      </button>

      <FileDrop
        onFile={(file) => {
          // Kept for the apply step: the picked file has to survive the preview,
          // and the input is cleared as soon as its dialog closes.
          setPending(file)
          setResult(null)
          send.mutate({ file, apply: false })
        }}
        busy={send.isPending}
        busyLabel={result ? 'Applying…' : 'Checking…'}
        label="Drop the filled workbook here, or click to choose"
        hint="Excel or CSV. Previewed first — nothing is written until you confirm."
        accept={WORKBOOK_EXTENSIONS.join(',')}
        validate={(file) => rejectionOf(file, { extensions: WORKBOOK_EXTENSIONS })}
      />

      {preview ? (
        <div className="space-y-2 rounded border border-navy-600 p-3">
          {errors.length ? (
            <Banner kind="warn">
              {errors.length} row error(s): {errors.slice(0, 3).join('; ')}
            </Banner>
          ) : null}

          <div className="text-sm font-medium text-white">
            {result?.applied ? `Applied ${num(preview.applied)} change(s)` : 'Preview of changes'}
          </div>

          <div className="max-h-40 space-y-0.5 overflow-y-auto text-xs text-navy-300">
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
              className="btn-primary text-sm"
              disabled={send.isPending}
              onClick={() => send.mutate({ file: pending, apply: true })}
            >
              <CircleCheck className="h-4 w-4" /> Confirm &amp; apply
            </button>
          ) : null}

          {!result?.applied && summaryTotal === 0 ? (
            <div className="text-xs text-navy-500">No changes detected in the uploaded file.</div>
          ) : null}
        </div>
      ) : null}
    </SubCard>
  )
}

// ---------------------------------------------------------------------------
// Step 2C — detect from system tables
// ---------------------------------------------------------------------------

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

/**
 * What the sweep found, in words — the console's `renderSyncResult`
 * (`console.js:158-190`) and the defect it documents.
 *
 * "0 changes" is the COMMON result and it is NOT a failure: the sweep only
 * advances a source when Databricks lineage or job history shows real activity
 * against it, so on an estate whose tables are not yet mapped to portfolio sources
 * the honest answer is "scanned, nothing to advance". The old wording — "Would
 * change: 0 data source(s)" — read exactly like a button that did nothing, which
 * is how it got reported as a bug.
 */
function DetectionSummary({ detection, applied }: { detection: DetectionResult; applied: boolean }) {
  const assets = detection.asset_changes ?? []
  const useCases = detection.uc_changes ?? []
  const probes = detection.available ?? {}
  const anyReadable = Object.keys(probes).length === 0 || Object.values(probes).some(Boolean)

  return (
    <div className="space-y-2">
      {Object.keys(probes).length ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-navy-400">
          System tables:
          {Object.entries(probes).map(([table, reachable]) => (
            <span key={table} className={reachable ? 'badge-low' : 'badge-critical'}>
              {table}
            </span>
          ))}
        </div>
      ) : null}

      {!anyReadable ? (
        <Banner kind="warn">
          System tables are not readable by this app&apos;s service principal, so there is nothing
          to detect from. The checks in step 1 show the GRANT that fixes it.
        </Banner>
      ) : assets.length === 0 && useCases.length === 0 ? (
        <Banner kind="info">
          Scanned successfully — no sources to advance. This only promotes a source when Databricks
          lineage or job history shows real activity against it, so nothing changing usually means
          the estate&apos;s tables are not yet mapped to sources in this portfolio.
        </Banner>
      ) : (
        <Banner kind="ok">
          {applied ? 'Applied' : 'Would change'}: {assets.length} data source(s), {useCases.length}{' '}
          use case(s).
          {applied ? '' : ' Nothing has been written yet — use Apply to commit.'}
        </Banner>
      )}

      {assets.length ? (
        <div className="max-h-40 overflow-y-auto text-xs text-navy-300">
          {assets.slice(0, 8).map((change, index) => (
            <div key={change.id ?? index}>
              · {change.label}: → <span className="text-success">landed</span>
            </div>
          ))}
        </div>
      ) : null}

      {/* Always shown: evidence that work actually happened, which is what makes
          a zero-change result readable as a result rather than as a no-op. */}
      {detection.notes?.length ? (
        <div className="text-xs text-navy-500">
          <div className="mb-0.5 font-medium text-navy-400">What the scan looked at</div>
          {detection.notes.map((note, index) => (
            <div key={index}>{note}</div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function DetectCard() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [askingConsent, setAskingConsent] = useState(false)
  const [consented, setConsented] = useState(false)
  const [detection, setDetection] = useState<{ data: DetectionResult; applied: boolean } | null>(
    null,
  )

  const detect = useMutation({
    mutationFn: (apply: boolean) =>
      (api.liveSync(apply) as Promise<DetectionResult>).then((data) => ({ data, apply })),
    onSuccess: ({ data, apply }) => {
      setDetection({ data, applied: apply })
      setAskingConsent(false)
      // Only an apply wrote anything; a preview must not invalidate, or the
      // screen refetches to say the same thing and looks like it changed data.
      if (apply) {
        queryClient.invalidateQueries({ queryKey: ['data-assets'] })
        queryClient.invalidateQueries({ queryKey: ['use-cases'] })
        queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      }
    },
    onError: (error) => reportError(error, 'Could not scan the system tables.'),
    ...NO_RETRY,
  })

  return (
    <SubCard>
      <div className="flex items-center gap-2">
        <Cloud className="h-4 w-4 text-info" />
        <h4 className="text-sm font-semibold text-white">C · Detect from system tables</h4>
      </div>
      <p className="text-xs text-navy-400">
        Reads lineage and job history to auto-advance sources that show real activity, and marks
        use cases live where consumption exists. Read-only against Databricks, preview-first, and
        only with your explicit consent.
      </p>

      <button className="btn-primary text-sm" onClick={() => setAskingConsent(true)}>
        <Cloud className="h-4 w-4" /> Populate from Databricks (auto)
      </button>

      {askingConsent ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setAskingConsent(false)} />
          <div className="animate-scale-in card relative w-full max-w-lg">
            <div className="mb-2 flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-info" />
              <h3 className="text-lg font-bold">Permission required</h3>
            </div>
            <p className="mb-2 text-sm text-navy-300">
              This will run <strong>read-only</strong> queries against your Databricks system tables
              and SQL warehouse to detect real usage:
            </p>
            <ul className="mb-3 list-disc space-y-0.5 pl-5 text-xs text-navy-400">
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
            <p className="mb-3 text-xs text-navy-400">
              Preview reports what it found and writes nothing. Apply then advances ingestion
              status for detected landed sources and marks use cases live where consumption exists.
              Nothing is deleted; every change is shown and audited. If system tables aren&apos;t
              accessible in this workspace, it will report that and make no changes.
            </p>
            <label className="mb-3 flex cursor-pointer items-center gap-2 text-sm text-navy-200">
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
            <div className="flex flex-wrap justify-end gap-2">
              <button className="btn-secondary text-sm" onClick={() => setAskingConsent(false)}>
                Cancel
              </button>
              <button
                className="btn-secondary text-sm"
                disabled={!consented || detect.isPending}
                onClick={() => detect.mutate(false)}
              >
                {detect.isPending ? 'Scanning…' : 'Preview'}
              </button>
              <button
                className="btn-primary text-sm"
                disabled={!consented || detect.isPending}
                onClick={() => detect.mutate(true)}
              >
                {detect.isPending ? 'Scanning…' : 'Run detection & apply'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {detection ? (
        <div className="rounded border border-navy-600 p-3">
          <DetectionSummary detection={detection.data} applied={detection.applied} />
          {!detection.applied ? (
            <button
              className="btn-primary mt-2 text-sm"
              disabled={detect.isPending}
              onClick={() => detect.mutate(true)}
            >
              <CircleCheck className="h-4 w-4" /> Apply these changes
            </button>
          ) : null}
        </div>
      ) : null}
    </SubCard>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — enrich and normalize
// ---------------------------------------------------------------------------

function EnrichStep({
  hasInventory,
  state,
}: {
  hasInventory: boolean
  state: StepState
}) {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [company, setCompany] = useState('')
  const [maxRows, setMaxRows] = useState('')
  const [note, setNote] = useState<{ kind: 'ok' | 'warn'; message: string } | null>(null)

  const body = () => ({
    company_name: company.trim() || null,
    max_rows: maxRows ? Number(maxRows) : null,
  })

  const done = (message: string, kind: 'ok' | 'warn' = 'ok') => {
    queryClient.invalidateQueries({ queryKey: ['ingestion-summary'] })
    setNote({ kind, message })
  }

  const enrichSchemas = useMutation({
    mutationFn: () => api.enrichSchemas(body()),
    onSuccess: (result) => done(`${num(result.schemas_enriched_total)} schema(s) now enriched.`),
    onError: (error) => reportError(error, 'Could not enrich the schemas.'),
    ...NO_RETRY,
  })

  const enrichTables = useMutation({
    mutationFn: () => api.enrichTables(body()),
    onSuccess: (result) =>
      // A partial run is a real outcome, not a failure: `failOnError=>false` lets
      // individual model calls fail while the rest of the batch succeeds.
      done(
        `${num(result.tables_enriched_total)} table(s) enriched` +
          (result.staged_rows
            ? ` (${num(result.staged_ok)} of ${num(result.staged_rows)} model calls succeeded this run`
            : '') +
          (result.staged_errors ? `, ${num(result.staged_errors)} failed).` : ').'),
        result.staged_errors ? 'warn' : 'ok',
      ),
    onError: (error) => reportError(error, 'Could not enrich the tables.'),
    ...NO_RETRY,
  })

  const canonicalize = useMutation({
    mutationFn: api.canonicalizeSources,
    onSuccess: (result) =>
      done(
        `${num(result.distinct_labels)} distinct label(s); ${num(result.newly_resolved)} newly ` +
          `resolved (exact ${num(result.exact)}, normalized ${num(result.normalized)}, ` +
          `AI ${num(result.llm)}, unmapped ${num(result.other)}).` +
          (result.other ? ' Review the unmapped ones under Source mapping.' : ''),
      ),
    onError: (error) => reportError(error, 'Could not normalize the source labels.'),
    ...NO_RETRY,
  })

  const busy = enrichSchemas.isPending || enrichTables.isPending || canonicalize.isPending

  return (
    <Step
      n={3}
      state={state}
      locked={!hasInventory}
      title="Enrich and normalize"
      why="Raw table names don't say what a system is. Enrichment names each table in business terms; normalization then collapses the free-text labels — otherwise ~1,000 distinct strings describe ~50 real systems and every rollup is noise."
    >
      {hasInventory ? (
        <>
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[220px] flex-1">
              <label htmlFor="onboarding-company" className="mb-1 block text-xs text-navy-300">
                Company name (prompt context)
              </label>
              <input
                id="onboarding-company"
                name="onboarding-company"
                className="input-field"
                placeholder="e.g. Eversource Energy"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
              />
            </div>
            <div className="w-[160px]">
              <label htmlFor="onboarding-maxrows" className="mb-1 block text-xs text-navy-300">
                Cap tables (blank = all)
              </label>
              <input
                id="onboarding-maxrows"
                name="onboarding-maxrows"
                className="input-field"
                type="number"
                min={1}
                placeholder="500"
                value={maxRows}
                onChange={(event) => setMaxRows(event.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              className="btn-secondary text-sm"
              disabled={busy}
              aria-busy={enrichSchemas.isPending}
              onClick={() => enrichSchemas.mutate()}
            >
              {enrichSchemas.isPending ? 'Enriching…' : 'Enrich schemas'}
            </button>
            <button
              className="btn-primary text-sm"
              disabled={busy}
              aria-busy={enrichTables.isPending}
              onClick={() => enrichTables.mutate()}
            >
              <Sparkles className="h-4 w-4" />
              {enrichTables.isPending ? 'Enriching…' : 'Enrich tables'}
            </button>
            <button
              className="btn-secondary text-sm"
              disabled={busy}
              aria-busy={canonicalize.isPending}
              onClick={() => canonicalize.mutate()}
            >
              {canonicalize.isPending ? 'Normalizing…' : 'Normalize sources'}
            </button>
          </div>

          <p className="text-xs text-navy-500">
            Enrichment spend scales with table count — cap the first run to check quality and cost.
            Only the long tail of normalization costs an LLM call.
          </p>

          {note ? <Banner kind={note.kind}>{note.message}</Banner> : null}
        </>
      ) : (
        <p className="text-xs text-navy-500">Upload an inventory in step 2 first.</p>
      )}
    </Step>
  )
}

// ---------------------------------------------------------------------------
// Step 4 — attribution
// ---------------------------------------------------------------------------

function AttributeStep({
  hasEnrichment,
  state,
}: {
  hasEnrichment: boolean
  state: StepState
}) {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()
  const [advance, setAdvance] = useState(false)
  const [result, setResult] = useState<{
    message: string
    advanced: { id?: number; label: string; from?: string; to?: string }[]
    unmatched: string[]
  } | null>(null)

  const attribute = useMutation({
    mutationFn: () => api.attributeDiscovered(advance),
    onSuccess: (response) => {
      // Attribution rewrites discovered counts on every matched asset and, with
      // `advance_status`, their ingestion status — which moves readiness and so
      // every derived total.
      invalidatePortfolio(queryClient)
      queryClient.invalidateQueries({ queryKey: ['ingestion-summary'] })
      setResult({
        message:
          `${num(response.assets_matched)} catalog module(s) matched from ` +
          `${num(response.canonicals_discovered)} discovered source system(s).`,
        advanced: response.advanced ?? [],
        unmatched: response.unmatched_canonicals ?? [],
      })
    },
    onError: (error) => reportError(error, 'Could not attribute the discovered tables.'),
    ...NO_RETRY,
  })

  return (
    <Step
      n={4}
      state={state}
      locked={!hasEnrichment}
      title="Connect it to the portfolio"
      why={
        'This is the step that makes the rest of the app move: attributing discovered tables to ' +
        'catalog modules is what turns "we have 40,000 tables" into "these use cases are now ' +
        'shovel-ready".'
      }
    >
      {hasEnrichment ? (
        <>
          <label
            htmlFor="onboarding-advance"
            className="flex cursor-pointer items-center gap-2 text-sm text-navy-200"
          >
            <input
              id="onboarding-advance"
              name="onboarding-advance"
              type="checkbox"
              checked={advance}
              onChange={(event) => setAdvance(event.target.checked)}
            />
            Advance ingestion status to &ldquo;landed&rdquo;
          </label>

          <button
            className="btn-primary text-sm"
            disabled={attribute.isPending}
            aria-busy={attribute.isPending}
            onClick={() => attribute.mutate()}
          >
            {attribute.isPending ? 'Attributing…' : 'Attribute to the catalog'}
          </button>

          <p className="text-xs text-navy-500">
            Advancing status moves readiness and therefore the roadmap, so it is off by default —
            and caps at <em>landed</em>, because finding tables proves data exists, not that it is
            curated.
          </p>

          {result ? (
            <div className="space-y-2">
              <Banner kind="ok">{result.message}</Banner>
              {result.advanced.length ? (
                <SubCard>
                  <h4 className="text-sm font-semibold text-white">Status advanced</h4>
                  <div className="max-h-40 space-y-0.5 overflow-y-auto text-xs text-navy-300">
                    {result.advanced.map((row, index) => (
                      <div key={row.id ?? index}>
                        · {row.label}: {row.from} → <span className="text-success">{row.to}</span>
                      </div>
                    ))}
                  </div>
                </SubCard>
              ) : null}
              {result.unmatched.length ? (
                <SubCard>
                  <h4 className="text-sm font-semibold text-white">
                    Discovered but not in the catalog
                  </h4>
                  <p className="text-xs text-navy-400">
                    Add these as data assets, or map them under Source mapping.
                  </p>
                  <p className="font-mono text-xs text-navy-300">{result.unmatched.join(', ')}</p>
                </SubCard>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <p className="text-xs text-navy-500">Run enrichment in step 3 first.</p>
      )}
    </Step>
  )
}

// ---------------------------------------------------------------------------
// Step 5 — what next
// ---------------------------------------------------------------------------

/**
 * The console's "5 · Then what" link block, as in-app navigation.
 *
 * These were `<a href="#coverage">` hash links into the console's own router, and
 * one was `<a href="/">` back to the SPA — a full page load that threw away every
 * cached query. There is one app now, so they are `setTab` calls: §5's note that
 * "viewStart's next-steps link block becomes ordinary in-app nav once there's one
 * app". `taxonomy` is still a `ComingSoon` stub in App.tsx, which is deliberate —
 * linking to it is how the stub gets noticed and filled, and a dead hash link
 * would not.
 */
const NEXT_STEPS: { tab: TabId; label: string; why: string }[] = [
  {
    tab: 'coverage',
    label: 'Coverage & gaps',
    why: 'which data needs are met per line of business, and what you already have that nothing uses.',
  },
  {
    tab: 'portfolio',
    label: 'Use cases',
    why: 'browse the catalog, add what fits, and see readiness and value per use case.',
  },
  {
    tab: 'taxonomy',
    label: 'Taxonomy',
    why: 'classify how sources arrive and how critical they are.',
  },
  {
    tab: 'roadmap',
    label: 'Roadmap',
    why: 'sequence the work by what is actually ready to build.',
  },
]

function NextSteps({ setTab }: { setTab: (tab: TabId) => void }) {
  return (
    <Step
      n={5}
      state="idle"
      title="Then what"
      why="Once data is registered, the rest of the app has something to reason about."
    >
      <ul className="space-y-1.5 text-sm">
        {NEXT_STEPS.map((step) => (
          <li key={step.tab}>
            <button
              className="font-medium text-info hover:underline"
              onClick={() => setTab(step.tab)}
            >
              {step.label}
            </button>
            <span className="text-navy-400"> — {step.why}</span>
          </li>
        ))}
      </ul>
    </Step>
  )
}

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

export interface OnboardingViewProps {
  /** In-app navigation for step 5. Owned by `App.tsx`, which owns tab state. */
  setTab: (tab: TabId) => void
}

export default function OnboardingView({ setTab }: OnboardingViewProps) {
  const status = useQuery({ queryKey: ['setup-status'], queryFn: api.setupStatus })
  const inventory = useQuery({ queryKey: ['ingestion-summary'], queryFn: api.ingestionSummary })

  // The probes decide which steps unlock, so a failure to READ them is different
  // from a failed probe: without a status there is nothing to lock steps ON, and
  // rendering an all-unlocked wizard would invite five failing clicks.
  if (status.isLoading) {
    return (
      <div className="py-8 text-center text-sm text-navy-400" role="status">
        Checking your workspace…
      </div>
    )
  }

  if (status.isError || !status.data) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-bold text-white">Get started</h2>
        <Banner kind="err">
          {status.error instanceof Error && status.error.message
            ? `Could not read setup status: ${status.error.message}`
            : 'Could not read setup status.'}
        </Banner>
      </div>
    )
  }

  const setup = status.data
  // An unread inventory is treated as unconfigured rather than fatal: the workbook
  // path (2B) works without discovery at all, so a failed summary must not take the
  // whole wizard down with it.
  const summary: IngestionSummaryResponse = inventory.data ?? { configured: false }
  const hasInventory = (summary.tables ?? 0) > 0
  const hasEnrichment = (summary.enriched_tables ?? 0) > 0

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Rocket className="h-5 w-5 text-lava" />
          <h2 className="font-bold text-white">Get started</h2>
        </div>
        <p className="mt-1 max-w-[80ch] text-sm text-navy-400">
          Everything needed to go from an empty install to a populated portfolio, in order. Each
          step says why it matters and what it will change; nothing here runs on its own.
        </p>
      </div>

      <HealthStep status={setup} state={setup.ready ? 'done' : 'active'} />

      <Step
        n={2}
        state={hasInventory ? 'done' : setup.ready ? 'active' : 'idle'}
        title="Tell the app what data you have"
        why="Three ways in, and they compose — most utilities do all three. Sweep the estate to discover what exists, use the workbook to record the judgement calls only a person can make, and detect from system tables what is demonstrably in use."
      >
        <SweepCard inventory={summary} />
        <WorkbookCard />
        <DetectCard />
      </Step>

      <EnrichStep
        hasInventory={hasInventory}
        state={hasEnrichment ? 'done' : hasInventory ? 'active' : 'idle'}
      />

      <AttributeStep hasEnrichment={hasEnrichment} state={hasEnrichment ? 'active' : 'idle'} />

      <NextSteps setTab={setTab} />
    </div>
  )
}
