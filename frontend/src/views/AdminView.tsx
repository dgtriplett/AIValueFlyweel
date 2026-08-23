// Admin — instance state and the controls that change it.
//
// Ported from `console.js:1934-2126` (`viewAdmin`). Four surfaces: Demo Mode
// (load/reset the seeded portfolio), Genie (provision the space / refresh the
// mirror), Databricks sync (advance sources and use cases from system tables), and
// housekeeping (reclaim expired previews and consumed confirm tokens).
//
// AUTHZ IS SERVER-SIDE, AND THIS VIEW DOES NOT SECOND-GUESS IT
// ------------------------------------------------------------
// `POST /demo/load`, `POST /demo/reset` and `POST /genie/provision` all call
// `require_admin` after the Phase 0 hardening and fail closed with a 403. There is
// deliberately no `is_admin` endpoint the client could trust, so this view does NOT
// gate them client-side — it renders the affordance, attempts the call, and on a
// 403 surfaces the server's `detail` verbatim through the shared error toast. The
// UI's only job is to CALL correctly (every request through the account-scoped
// `http` client, never a raw `fetch` or an anchor `href` that would drop the
// account header, §4.1) and to handle the server's refusal gracefully.
//
// THE DESTRUCTIVE OPERATIONS ARE CONFIRM-GATED
// --------------------------------------------
// The console guarded demo-load / demo-reset behind a `window.confirm()`. Here they
// go through the shared `<ConfirmCard>` — the same primitive the token-spending
// agent flows use — driven in its LOCAL-ACTION mode: no confirm token exists for
// these endpoints, so the card is handed a `data` describing the change and an
// `onApprove` that runs the mutation. Genie provision is confirm-gated the same way
// because it mirrors the portfolio and builds a space (a minute of work), and a
// second click while it is in flight should not start a second one — the card's
// single-approve state machine prevents that. Every destructive mutation spreads
// `NO_RETRY`: an automatic replay of a destructive write after an ambiguous failure
// is never correct.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Database, Sparkles, Trash2, Wand2 } from 'lucide-react'

import { api } from '../api'
import { Banner } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { DatabricksSyncButton } from '../components/DatabricksSyncButton'
import { useApiErrorToast } from '../components/Toasts'
import { isApiError } from '../lib/errors'
import { NO_RETRY } from '../lib/retry'
import type {
  ConfirmCardData,
  DemoStatusResponse,
  GenieProvisionResponse,
  GenieStatusResponse,
} from '../types'

/** Read a count off `/health`'s `counts`, which is a map OR an `{error}` object. */
function healthCount(counts: unknown, key: string): number | null {
  if (!counts || typeof counts !== 'object' || 'error' in (counts as object)) return null
  const value = (counts as Record<string, unknown>)[key]
  return typeof value === 'number' ? value : null
}

export function AdminView(): JSX.Element {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  // Which destructive card is open, if any. Only one at a time — the confirm cards
  // are exclusive because they all rewrite the same portfolio.
  const [pending, setPending] = useState<'demo-load' | 'demo-reset' | 'genie-provision' | null>(
    null,
  )
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [provisioned, setProvisioned] = useState<GenieProvisionResponse | null>(null)

  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health })

  // A 404 on /demo/status is the DESIGNED answer when DEMO_MODE is off — catch it and
  // render the disabled card rather than surfacing it as an error.
  const demoQuery = useQuery({
    queryKey: ['demo-status'],
    queryFn: async (): Promise<DemoStatusResponse & { available: boolean }> => {
      try {
        const status = await api.demoStatus()
        return { ...status, available: true }
      } catch (error) {
        if (isApiError(error) && error.status === 404) return { enabled: false, available: false }
        throw error
      }
    },
  })

  const genieQuery = useQuery({
    queryKey: ['genie-status'],
    // Genie status is chrome; a failure means "we could not tell", which renders as
    // the not-configured card rather than an error page.
    queryFn: async (): Promise<GenieStatusResponse> => {
      try {
        return await api.genieStatus()
      } catch {
        return { configured: false }
      }
    },
  })

  const health = healthQuery.data
  const demo = demoQuery.data
  const genie = genieQuery.data

  // After a destructive write, the header strip's counts are stale — invalidate the
  // portfolio queries the shell reads so they refetch under the (now-changed) data.
  const invalidatePortfolio = () => {
    queryClient.invalidateQueries({ queryKey: ['health'] })
    queryClient.invalidateQueries({ queryKey: ['use-cases'] })
    queryClient.invalidateQueries({ queryKey: ['data-assets'] })
    queryClient.invalidateQueries({ queryKey: ['lobs'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  const demoLoad = useMutation({
    mutationFn: () => api.demoLoad(),
    ...NO_RETRY,
    onSuccess: (result) => {
      setPending(null)
      invalidatePortfolio()
      const count = result.counts?.use_cases
      setNotice({
        kind: 'ok',
        text: `Showcase data loaded${count != null ? ` (${count} use cases)` : ''}.`,
      })
    },
    onError: (error) => {
      setPending(null)
      setNotice(null)
      reportError(error, 'Could not load the showcase data.')
    },
  })

  const demoReset = useMutation({
    mutationFn: () => api.demoReset(),
    ...NO_RETRY,
    onSuccess: (result) => {
      setPending(null)
      invalidatePortfolio()
      const count = result.counts?.use_cases
      setNotice({
        kind: 'ok',
        text: `Reset to clean day-1${count != null ? ` (${count} use cases)` : ''}.`,
      })
    },
    onError: (error) => {
      setPending(null)
      setNotice(null)
      reportError(error, 'Could not reset the portfolio.')
    },
  })

  const provision = useMutation({
    mutationFn: () => api.genieProvision(),
    ...NO_RETRY,
    onSuccess: (result) => {
      setPending(null)
      setProvisioned(result)
      queryClient.invalidateQueries({ queryKey: ['genie-status'] })
    },
    onError: (error) => {
      setPending(null)
      reportError(error, 'Could not create the Genie space.')
    },
  })

  const cleanup = useMutation({
    mutationFn: () => api.generateCleanup(),
    onSuccess: (result) => {
      setNotice({
        kind: 'ok',
        text: `Removed ${result.previews_deleted ?? 0} preview(s) and ${result.tokens_deleted ?? 0} token(s).`,
      })
    },
    onError: (error) => {
      setNotice(null)
      reportError(error, 'Could not clean up expired records.')
    },
  })

  // The local-action confirm cards. `before`/`after` are display-only; the effect is
  // the mutation `onApprove` runs. Kept small and explicit so the card can render its
  // one-line diff of what changes.
  // The token is a placeholder: these endpoints do NOT issue confirm tokens, so the
  // card runs in local-action mode (`onApprove`) and never POSTs `/confirm/{token}`.
  // A stable non-empty string keeps `data-ga-confirm-token` meaningful in the DOM.
  const demoLoadCard: ConfirmCardData = {
    token: 'demo-load',
    intent: 'demo_load',
    summary: 'Replace the portfolio with the showcase dataset?',
    after: { action: 'load showcase data', scope: 'this account' },
  }
  const demoResetCard: ConfirmCardData = {
    token: 'demo-reset',
    intent: 'demo_reset',
    summary: 'Reset the portfolio to pristine day-1?',
    after: { action: 'reset to clean day-1', scope: 'this account' },
  }
  const provisionCard: ConfirmCardData = {
    token: 'genie-provision',
    intent: 'genie_provision',
    summary: 'Create a Genie space over the portfolio mirror?',
    after: {
      action: 'mirror the portfolio and build a Genie space',
      target: genie?.mirror_target ?? 'the mirror schema',
    },
  }

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Wand2 className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Administration</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          Instance state and the controls that change it. Everything here affects live data, so
          each destructive action says what it will do first. These operations are admin-gated on
          the server — if you are not an operator, the server refuses and says so.
        </p>
      </div>

      {notice ? <Banner kind={notice.kind}>{notice.text}</Banner> : null}

      {/* ---- This instance ---------------------------------------------------- */}
      <div className="card space-y-2">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-navy-300" />
          <h3 className="font-semibold text-white">This instance</h3>
        </div>
        {healthQuery.isLoading ? (
          <p className="text-sm text-navy-400">Loading instance state…</p>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
              <div>
                <div className="text-navy-500 text-xs">Lakebase</div>
                <div className="text-white">
                  {health?.db_connected ? 'connected' : 'demo mode'}
                </div>
              </div>
              <div>
                <div className="text-navy-500 text-xs">Use cases</div>
                <div className="text-white">{healthCount(health?.counts, 'use_cases') ?? '—'}</div>
              </div>
              <div>
                <div className="text-navy-500 text-xs">Data assets</div>
                <div className="text-white">
                  {healthCount(health?.counts, 'data_assets') ?? '—'}
                </div>
              </div>
            </div>
            <p className="text-xs text-navy-500 pt-1">
              Environment <code className="text-navy-300">{health?.environment ?? '—'}</code>
              {health?.demo_mode ? ' · demo mode' : ''}
            </p>
          </>
        )}
      </div>

      {/* ---- Demo Mode -------------------------------------------------------- */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-navy-300" />
          <h3 className="font-semibold text-white">Demo Mode</h3>
        </div>
        {demo?.available ? (
          <>
            <p className="text-sm text-navy-400 max-w-[78ch]">
              Flips this instance&apos;s Lakebase between the two seeded states. Runs its DML inside
              one transaction — a failure rolls back rather than leaving the portfolio
              half-populated.
            </p>
            <p className="text-sm">
              Current state:{' '}
              <span className="text-white font-semibold">{demo.mode ?? 'unknown'}</span>
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn-primary text-sm"
                disabled={pending != null || demoLoad.isPending}
                onClick={() => {
                  setNotice(null)
                  setPending('demo-load')
                }}
              >
                Load showcase data
              </button>
              <button
                className="btn-secondary text-sm"
                disabled={pending != null || demoReset.isPending}
                onClick={() => {
                  setNotice(null)
                  setPending('demo-reset')
                }}
              >
                Reset to clean day-1
              </button>
            </div>
            <p className="text-xs text-navy-500">
              <strong>Both are destructive</strong> — they replace the portfolio. Don&apos;t run
              them on an instance holding a customer&apos;s real data.
            </p>
          </>
        ) : (
          <Banner kind="info">
            Disabled on this instance. It is gated behind the <code>DEMO_MODE</code> env var, which
            ships &apos;off&apos; so a customer install cannot reset its own portfolio. To enable it
            here, redeploy with <code>scripts/deploy.py --demo-mode on</code>.
          </Banner>
        )}

        {pending === 'demo-load' ? (
          <ConfirmCard
            data={demoLoadCard}
            approveLabel={demoLoad.isPending ? 'Loading…' : 'Load showcase data'}
            onApprove={() => demoLoad.mutateAsync()}
            onCancel={() => setPending(null)}
          />
        ) : null}

        {pending === 'demo-reset' ? (
          <ConfirmCard
            data={demoResetCard}
            approveLabel={demoReset.isPending ? 'Resetting…' : 'Reset to clean day-1'}
            onApprove={() => demoReset.mutateAsync()}
            onCancel={() => setPending(null)}
          />
        ) : null}
      </div>

      {/* ---- Genie ------------------------------------------------------------ */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-navy-300" />
          <h3 className="font-semibold text-white">Genie</h3>
        </div>
        {genie?.configured ? (
          <>
            <p className="text-sm text-navy-400 max-w-[78ch]">
              Ask answers questions in natural language over the mirrored portfolio. The mirror is a
              snapshot, not a live view — refresh it after the portfolio changes.
            </p>
            {genie.space_id ? (
              <p className="text-sm text-navy-300">
                Space <code className="text-navy-200">{genie.space_id}</code>
                {genie.space_url ? (
                  <>
                    {' · '}
                    <a
                      className="text-info hover:underline"
                      href={genie.space_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      open in the workspace
                    </a>
                  </>
                ) : null}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <DatabricksSyncButton />
            </div>
            <p className="text-xs text-navy-500 max-w-[78ch]">
              To rebuild the space itself, clear <code>GENIE_SPACE_ID</code> and redeploy; it is not
              replaced automatically because the instructions and saved questions in it are not
              stored here.
            </p>
          </>
        ) : genie?.can_provision ? (
          <>
            <Banner kind="info">
              No Genie space yet, so Ask returns a placeholder. This can create one — it mirrors the
              portfolio into <code>{genie.mirror_target ?? 'the mirror schema'}</code> and builds a
              space over it, seeded with the units, the readiness vocabulary and a set of starter
              questions.
            </Banner>
            <button
              className="btn-primary text-sm"
              disabled={pending != null || provision.isPending}
              onClick={() => {
                setNotice(null)
                setPending('genie-provision')
              }}
            >
              Create the Genie space
            </button>
            <p className="text-xs text-navy-500 max-w-[78ch]">
              Takes about a minute — most of it is the mirror. One manual step is left at the end: an
              app cannot rewrite its own configuration, so you will need to set the returned space id
              and redeploy.
            </p>
          </>
        ) : (
          <Banner kind="warn">
            No Genie space, and none can be created from here: no SQL warehouse is bound to this app.
            Bind one in the app&apos;s resources (or set <code>DATABRICKS_WAREHOUSE_ID</code>) and
            redeploy — a Genie space runs its queries on a warehouse, so there is nothing to point it
            at until then.
          </Banner>
        )}

        {pending === 'genie-provision' ? (
          <ConfirmCard
            data={provisionCard}
            approveLabel={provision.isPending ? 'Creating the space…' : 'Create the Genie space'}
            onApprove={() => provision.mutateAsync()}
            onCancel={() => setPending(null)}
          />
        ) : null}

        {provisioned ? (
          <div className="card bg-navy-900 space-y-2">
            <Banner kind="ok">
              Created &quot;{provisioned.title ?? 'the space'}&quot; over{' '}
              {(provisioned.tables ?? []).length} table(s), seeded with {provisioned.rules ?? 0}{' '}
              rules and {provisioned.starter_questions ?? 0} starter questions.
            </Banner>
            <div>
              <div className="text-sm font-semibold text-white">Space id</div>
              {/* `user-select:all` so the operator can select-and-copy it in one click — the
                  next thing they do is paste it into the deploy config. */}
              <p>
                <code className="text-info" style={{ userSelect: 'all' }}>
                  {provisioned.space_id}
                </code>
              </p>
            </div>
            {provisioned.url ? (
              <p className="text-sm">
                <a
                  className="text-info hover:underline"
                  href={provisioned.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open it in the workspace →
                </a>
              </p>
            ) : null}
            {provisioned.next_step ? (
              <>
                <div className="text-sm font-semibold text-white">One step left</div>
                <p className="text-sm text-navy-400">{provisioned.next_step}</p>
              </>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ---- Databricks sync -------------------------------------------------- */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <Cloud className="w-4 h-4 text-navy-300" />
          <h3 className="font-semibold text-white">Databricks sync</h3>
        </div>
        <p className="text-sm text-navy-400 max-w-[78ch]">
          Reads system tables to auto-advance data sources that show real lineage, and use cases
          whose linked jobs are running. Read-only against Databricks; writes only to this
          app&apos;s own state.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <DatabricksSyncButton />
        </div>
      </div>

      {/* ---- Housekeeping ----------------------------------------------------- */}
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <Trash2 className="w-4 h-4 text-navy-300" />
          <h3 className="font-semibold text-white">Housekeeping</h3>
        </div>
        <p className="text-sm text-navy-400 max-w-[78ch]">
          Deletes expired generation previews and consumed confirm tokens. Safe — expiry is already
          enforced at read time, so this only reclaims space.
        </p>
        <button
          className="btn-secondary text-sm"
          disabled={cleanup.isPending}
          onClick={() => {
            setNotice(null)
            cleanup.mutate()
          }}
        >
          {cleanup.isPending ? 'Cleaning…' : 'Clean up expired records'}
        </button>
      </div>
    </div>
  )
}

export default AdminView
