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
import { Cloud, Database, Sparkles, Trash2, Users, UserPlus, Wand2, X } from 'lucide-react'

import { api } from '../api'
import { useRole } from '../context/RoleContext'
import { Banner } from '../components/Banner'
import { ConfirmCard } from '../components/ConfirmCard'
import { DatabricksSyncButton } from '../components/DatabricksSyncButton'
import { useApiErrorToast } from '../components/Toasts'
import { isApiError } from '../lib/errors'
import { NO_RETRY } from '../lib/retry'
import type {
  AppUser,
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
  // Server `require_admin` is the real gate on every /api/users endpoint; this only
  // decides whether to RENDER the section, for good UX. A non-admin who forced it
  // open would still get a 403 the moment any call fired.
  const { isAdmin } = useRole()

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

      {/* ---- Users ------------------------------------------------------------ */}
      {isAdmin ? <UsersSection /> : null}

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


/**
 * Users — assign/change who is a PM / Executive / Admin (admin-portal/roles, Phase B).
 *
 * The whole section renders only for an admin (AdminView gates on `useRole().isAdmin`),
 * but the SERVER's `require_admin` on every /api/users endpoint is the real gate — this
 * view does not trust the client for authz, it just avoids showing controls that would
 * 403. Every call goes through the account-scoped `api` client (never a raw fetch), a
 * role change is a single PUT, and a removal is confirm-gated through the shared
 * <ConfirmCard> in local-action mode — the same primitive the demo-reset flow uses —
 * spreading NO_RETRY so an ambiguous failure never silently replays a destructive write.
 *
 * Env-allowlist admins (GRID_ATLAS_ADMINS) are shown with an '(env admin)' badge and
 * their role select disabled: the allowlist outranks the table, so their admin status
 * cannot be changed by editing a row, and pretending otherwise would be a lie.
 */
const ROLE_OPTIONS: AppUser['role'][] = ['pm', 'executive', 'admin']

function UsersSection(): JSX.Element {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  const [addEmail, setAddEmail] = useState('')
  const [addRole, setAddRole] = useState<AppUser['role']>('pm')
  // The email pending a confirm-gated removal, if any.
  const [removing, setRemoving] = useState<string | null>(null)

  const usersQuery = useQuery({ queryKey: ['users'], queryFn: api.listUsers })

  const invalidateUsers = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  const setRole = useMutation({
    mutationFn: ({ email, role }: { email: string; role: AppUser['role'] }) =>
      api.setUserRole(email, role),
    ...NO_RETRY,
    onSuccess: invalidateUsers,
    onError: (error) => reportError(error, 'Could not change that user\'s role.'),
  })

  const addUser = useMutation({
    mutationFn: ({ email, role }: { email: string; role: AppUser['role'] }) =>
      api.setUserRole(email, role),
    ...NO_RETRY,
    onSuccess: () => {
      setAddEmail('')
      setAddRole('pm')
      invalidateUsers()
    },
    onError: (error) => reportError(error, 'Could not add that user.'),
  })

  const removeUser = useMutation({
    mutationFn: (email: string) => api.removeUser(email),
    ...NO_RETRY,
    onSuccess: () => {
      setRemoving(null)
      invalidateUsers()
    },
    onError: (error) => {
      setRemoving(null)
      reportError(error, 'Could not remove that user.')
    },
  })

  const users = usersQuery.data ?? []

  const removeCard: ConfirmCardData = {
    token: 'remove-user',
    intent: 'user_role_revoke',
    summary: `Remove ${removing ?? 'this user'}\'s role? They revert to the 'pm' default.`,
    after: { action: 'remove the stored role', scope: removing ?? 'this user' },
  }

  const trimmedEmail = addEmail.trim()

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2">
        <Users className="w-4 h-4 text-navy-300" />
        <h3 className="font-semibold text-white">Users</h3>
      </div>
      <p className="text-sm text-navy-400 max-w-[78ch]">
        Assign who is a PM, an Executive, or an Admin on this instance. Roles are global
        (keyed by email), and every change here is admin-gated and audited on the server.
        Admins from the <code>GRID_ATLAS_ADMINS</code> allowlist are admin regardless of
        this table — their role is shown as <em>(env admin)</em> and cannot be changed here.
      </p>

      {usersQuery.isLoading ? (
        <p className="text-sm text-navy-400">Loading users…</p>
      ) : usersQuery.isError ? (
        <Banner kind="err">Could not load users. You may not be an administrator.</Banner>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-navy-500 border-b border-navy-800">
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Role</th>
                <th className="py-2 pr-3 font-medium">Granted by</th>
                <th className="py-2 pr-3 font-medium">Updated</th>
                <th className="py-2 font-medium sr-only">Remove</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-3 text-navy-500">
                    No managed users yet. Add one below.
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.email} className="border-b border-navy-900">
                    <td className="py-2 pr-3 text-white">
                      {user.email}
                      {user.is_env_admin ? (
                        <span className="ml-2 text-xs text-info">(env admin)</span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">
                      <select
                        className="bg-navy-900 border border-navy-700 rounded px-2 py-1 text-white text-sm disabled:opacity-50"
                        value={user.role}
                        disabled={user.is_env_admin || setRole.isPending}
                        onChange={(event) =>
                          setRole.mutate({
                            email: user.email,
                            role: event.target.value as AppUser['role'],
                          })
                        }
                      >
                        {ROLE_OPTIONS.map((role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 pr-3 text-navy-400">{user.granted_by ?? '—'}</td>
                    <td className="py-2 pr-3 text-navy-400">
                      {user.updated_at ? user.updated_at.slice(0, 10) : '—'}
                    </td>
                    <td className="py-2 text-right">
                      {user.is_env_admin ? (
                        <span className="text-xs text-navy-600">—</span>
                      ) : (
                        <button
                          className="text-navy-400 hover:text-lava disabled:opacity-50"
                          aria-label={`Remove ${user.email}`}
                          disabled={removeUser.isPending}
                          onClick={() => setRemoving(user.email)}
                        >
                          <X className="w-4 h-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Add user */}
      <div className="flex flex-wrap items-end gap-2 pt-1">
        <div className="flex-1 min-w-[16rem]">
          <label className="block text-xs text-navy-500 mb-1">Add user (email)</label>
          <input
            type="email"
            className="w-full bg-navy-900 border border-navy-700 rounded px-2 py-1 text-white text-sm"
            placeholder="person@utility.com"
            value={addEmail}
            onChange={(event) => setAddEmail(event.target.value)}
          />
        </div>
        <div>
          <label className="block text-xs text-navy-500 mb-1">Role</label>
          <select
            className="bg-navy-900 border border-navy-700 rounded px-2 py-1 text-white text-sm"
            value={addRole}
            onChange={(event) => setAddRole(event.target.value as AppUser['role'])}
          >
            {ROLE_OPTIONS.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn-primary text-sm inline-flex items-center gap-1"
          disabled={!trimmedEmail || addUser.isPending}
          onClick={() => addUser.mutate({ email: trimmedEmail, role: addRole })}
        >
          <UserPlus className="w-4 h-4" />
          {addUser.isPending ? 'Adding…' : 'Add user'}
        </button>
      </div>

      {removing ? (
        <ConfirmCard
          data={removeCard}
          approveLabel={removeUser.isPending ? 'Removing…' : 'Remove user'}
          onApprove={() => removeUser.mutateAsync(removing)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}
    </div>
  )
}

export default AdminView
