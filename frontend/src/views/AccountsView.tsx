// Accounts — the switcher that makes one deployment serve several utilities.
//
// Ported from `console.js:3163-3253` (`viewAccounts`). Each account owns its own
// company profile, calibrated assumptions, landed sources, research and knowledge
// base; the reference library of use cases and sources is shared.
//
// THE HIGHEST-RISK ITEM IN THE PHASE — TIER3_MIGRATION_PLAN.md §4.1
// -----------------------------------------------------------------
// Switching accounts writes the choice to localStorage (`lib/account.ts` reads it,
// the axios interceptor sends it on EVERY request). The console then did
// `location.reload()`, because the whole page had to re-read every screen under
// the new account. The SPA has a live react-query cache, and that cache is the
// hazard: a scoped view — the portfolio, the dashboards, the KB — holding the
// PREVIOUS account's rows after the header changed is exactly the tenant-mixing
// failure the account interceptor exists to prevent, surfaced one layer up.
//
// So on switch this does two things atomically before anything can refetch:
//   1. `setAccountId(id)` — persist the choice, so the interceptor sends the new
//      account on the very next request;
//   2. `queryClient.clear()` — drop every cached query, so nothing can render the
//      previous account's data. `clear()` rather than `invalidateQueries()`:
//      invalidate keeps stale data on screen while it refetches, which is the
//      window where two tenants' numbers can be seen together. Clearing removes it
//      outright and every mounted query refetches from empty under the new scope.
//
// A full reload would also work and is what the console did — but clearing the
// cache achieves the same guarantee without throwing away the app shell and the
// lobs/use-cases/assets the header strip reads, so it is the less disruptive
// correct option and the one §4.1 asks for when it is achievable. It is.
//
// ADMIN GATING IS SERVER-SIDE, AND SO IS THE ARCHIVED LIST
// --------------------------------------------------------
// `include_inactive=true` is admin-gated (`accounts.py:list_accounts`), as are
// create and make-default. The view attempts the admin request and, on a 403,
// falls back to the active-only list — a non-admin sees the switcher, not an
// error. Create / make-default surface the server's 403 `detail` if attempted
// without permission. There is no client-side is_admin gate the server trusts.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Check, Plus, Star } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { useApiErrorToast } from '../components/Toasts'
import { OPTION_STYLE, SELECT_CLASS } from '../constants'
import { isApiError } from '../lib/errors'
import { NO_RETRY } from '../lib/retry'
import { ACCOUNT_STORAGE_KEY } from '../lib/account'
import type { Account } from '../types'

/** The utility-type options the console offered, matching `viewAccounts`. */
const UTILITY_TYPES = [
  { value: 'investor-owned', label: 'Investor-owned' },
  { value: 'municipal', label: 'Municipal' },
  { value: 'cooperative', label: 'Cooperative' },
  { value: 'generation-only', label: 'Generation-only' },
] as const

/**
 * Persist the selected account id.
 *
 * Deliberately writes the SAME key `lib/account.ts` reads (`avf_account_id`), so
 * the interceptor picks it up on the next request. Guarded because a browser
 * blocking storage throws rather than returning — an unwritable selection is worth
 * reporting, not crashing the switch.
 */
export function setAccountId(id: number): boolean {
  try {
    localStorage.setItem(ACCOUNT_STORAGE_KEY, String(id))
    return true
  } catch {
    return false
  }
}

export function AccountsView(): JSX.Element {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  const [name, setName] = useState('')
  const [utilityType, setUtilityType] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [switchError, setSwitchError] = useState<string | null>(null)

  // Ask for archived accounts too. A 403 (non-admin) is the designed answer, so we
  // fall back to the active-only list rather than surfacing it as an error.
  const accountsQuery = useQuery({
    queryKey: ['accounts', 'include_inactive'],
    queryFn: async () => {
      try {
        return await api.accounts(true)
      } catch (error) {
        if (isApiError(error) && error.isForbidden) return api.accounts(false)
        throw error
      }
    },
  })

  const data = accountsQuery.data
  const accounts = data?.accounts ?? []
  const currentId = data?.current_account_id ?? null

  const create = useMutation({
    mutationFn: (body: { name: string; utility_type: string | null }) =>
      api.createAccount(body),
    // Admin-gated + `write`-limited; do not replay a create on an ambiguous failure.
    ...NO_RETRY,
    onSuccess: () => {
      setName('')
      setUtilityType('')
      setFormError(null)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
    onError: (error) => reportError(error, 'Could not add that account.'),
  })

  const makeDefault = useMutation({
    mutationFn: (id: number) => api.updateAccount(id, { make_default: true }),
    ...NO_RETRY,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['accounts'] }),
    onError: (error) => reportError(error, 'Could not change the default account.'),
  })

  const submit = () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setFormError('Give the utility a name.')
      return
    }
    setFormError(null)
    create.mutate({ name: trimmed, utility_type: utilityType || null })
  }

  /**
   * Switch the whole instance to another account.
   *
   * The critical path — see the file header. Persist, then CLEAR the cache so no
   * view keeps the previous account's rows, then invalidate the account list so
   * the "viewing" marker moves. Nothing here refetches under the old scope.
   */
  const switchTo = (id: number) => {
    if (!setAccountId(id)) {
      setSwitchError(
        'This browser is blocking local storage, so the account could not be saved. ' +
          'Enable storage for this site and try again.',
      )
      return
    }
    setSwitchError(null)
    // Drop every cached query so nothing renders the previous account's data.
    // Clearing (not invalidating) removes it outright rather than showing it stale
    // while a refetch runs — the window §4.1 warns against.
    queryClient.clear()
  }

  // A pre-migration install returns an empty list with a note rather than a table.
  const migrationNote = data?.note && accounts.length === 0 ? data.note : null

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <Building2 className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Accounts</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          One deployment, several utilities. Each account owns its own company profile,
          calibrated assumptions, landed sources, research and knowledge base — the reference
          library of use cases and sources is shared.
        </p>
      </div>

      <div className="card space-y-3">
        <h3 className="font-semibold text-white">Add an account</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="flex-1 min-w-[220px] rounded-md border border-navy-600 bg-navy-900 px-3 py-2 text-sm text-white"
            placeholder="Utility name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <select
            className={SELECT_CLASS}
            value={utilityType}
            onChange={(event) => setUtilityType(event.target.value)}
          >
            <option value="" style={OPTION_STYLE}>
              Type…
            </option>
            {UTILITY_TYPES.map((type) => (
              <option key={type.value} value={type.value} style={OPTION_STYLE}>
                {type.label}
              </option>
            ))}
          </select>
          <button
            className="btn-primary text-sm flex items-center gap-1.5"
            disabled={create.isPending}
            onClick={submit}
          >
            <Plus className="w-4 h-4" />
            {create.isPending ? 'Adding…' : 'Add account'}
          </button>
        </div>
        {formError ? <Banner kind="warn">{formError}</Banner> : null}
      </div>

      {switchError ? <Banner kind="err">{switchError}</Banner> : null}

      <QueryState
        query={accountsQuery}
        loading="Loading accounts…"
        empty={!migrationNote && accounts.length === 0}
        emptyMessage="No accounts yet."
      />

      {migrationNote ? <Banner kind="warn">{migrationNote}</Banner> : null}

      {accounts.length ? (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-navy-400 border-b border-navy-700">
                <th className="py-2 pr-3">Account</th>
                <th className="py-2 pr-3">Type</th>
                <th className="py-2 pr-3 text-right">Sources ready</th>
                <th className="py-2 pr-3">Researched</th>
                <th className="py-2 pr-3 text-right">KB</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {accounts.map((account: Account) => {
                const isCurrent = account.id === currentId
                return (
                  <tr
                    key={account.id}
                    className="border-b border-navy-800"
                    style={isCurrent ? { background: 'rgba(255,54,33,.07)' } : undefined}
                  >
                    <td className="py-2 pr-3">
                      <div className="font-semibold text-white">
                        {account.name}
                        {account.is_default ? (
                          <span className="ml-1.5 text-xs text-navy-400">default</span>
                        ) : null}
                        {!account.is_active ? (
                          <span className="ml-1.5 text-xs text-navy-400">archived</span>
                        ) : null}
                      </div>
                      <div className="text-xs text-navy-500">{account.slug}</div>
                    </td>
                    <td className="py-2 pr-3 text-navy-300">{account.utility_type || '—'}</td>
                    <td className="py-2 pr-3 text-right text-navy-300">
                      {account.sources_ready ?? 0} / {account.sources_tracked ?? 0}
                    </td>
                    <td className="py-2 pr-3 text-navy-300">
                      {account.researched ? 'yes' : 'no'}
                    </td>
                    <td className="py-2 pr-3 text-right text-navy-300">
                      {account.kb_articles ?? 0}
                    </td>
                    <td className="py-2">
                      <div className="flex items-center justify-end gap-2">
                        {isCurrent ? (
                          <span className="inline-flex items-center gap-1 text-xs text-success-400">
                            <Check className="w-3.5 h-3.5" /> viewing
                          </span>
                        ) : (
                          <button
                            className="btn-secondary text-xs"
                            onClick={() => switchTo(account.id)}
                          >
                            Switch to
                          </button>
                        )}
                        {!account.is_default && account.is_active ? (
                          <button
                            className="btn-secondary text-xs flex items-center gap-1"
                            disabled={makeDefault.isPending}
                            onClick={() => makeDefault.mutate(account.id)}
                          >
                            <Star className="w-3.5 h-3.5" /> Make default
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="text-xs text-navy-500 mt-3">
            Switching clears the app&apos;s cached data and reloads every view under the new
            account: the account is sent on every request, so it cannot be changed for one view
            only.
          </p>
        </div>
      ) : null}
    </div>
  )
}

export default AccountsView
