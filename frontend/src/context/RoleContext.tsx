// Role + persona context for the persona-aware UI (Phase A+).
//
// This is the LINCHPIN other phases build on. It fetches GET /api/me once on load
// and exposes:
//   * email: the platform-attributed identity (null when local/unauthenticated).
//   * isAdmin: whether the identity is on the GRID_ATLAS_ADMINS allowlist.
//   * isExecLocked: whether the user is forced into 'executive' persona (true when
//     role='executive' AND not admin).
//   * activePersona: INFERRED from the server-returned role (not self-selected),
//     EXCEPT admins may temporarily 'view as' another persona (that UI comes in a
//     later phase — for THIS phase, persona = role for non-admins, and admins
//     default to 'admin').
//
// PERSONA DETERMINATION (PHASE A):
//   - Non-admins: persona = role (inferred from stored role, NOT a dropdown).
//   - Admins: persona defaults to 'admin'; the 'view as' switcher comes in a later phase.
//   - If isExecLocked: forced 'executive' (cannot switch).

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'

export type Persona = 'admin' | 'pm' | 'executive'

/**
 * PHASE 4 — ADMIN LOCKDOWN: the central persona-coercion the whole UI agrees on.
 *
 * Persona is self-selected and persisted in localStorage, so a non-admin can end
 * up carrying a STALE or FORCED 'admin' persona (an old localStorage value, an
 * admin who lost the allowlist, a hand-edited storage key). The trusted fact is
 * `isAdmin`, sourced from GET /api/me — NOT the self-selected persona.
 *
 * `effectivePersona` folds those two facts into the one persona every consumer
 * (Header nav filtering AND App.tsx render/fallback) reads, so they cannot
 * disagree: an 'admin' persona held by a non-admin resolves to 'pm'. This is the
 * single choke point for "treat a non-admin as pm for all nav/render purposes".
 */
export function effectivePersona(persona: Persona, isAdmin: boolean): Persona {
  if (persona === 'admin' && !isAdmin) return 'pm'
  return persona
}

interface RoleContextValue {
  email: string | null
  isAdmin: boolean
  isExecLocked: boolean
  activePersona: Persona
  setPersona: (persona: Persona) => void
  isPreviewing: boolean
  loading: boolean
}

const RoleContext = createContext<RoleContextValue | null>(null)

const PERSONA_STORAGE_KEY = 'grid-atlas-persona'

function loadStoredPersona(): Persona | null {
  try {
    const stored = localStorage.getItem(PERSONA_STORAGE_KEY)
    if (stored === 'admin' || stored === 'pm' || stored === 'executive') {
      return stored
    }
  } catch {
    // localStorage may be unavailable (incognito, blocked)
  }
  return null
}

function storePersona(persona: Persona): void {
  try {
    localStorage.setItem(PERSONA_STORAGE_KEY, persona)
  } catch {
    // localStorage may be unavailable
  }
}

export function RoleProvider({ children }: { children: ReactNode }) {
  const [email, setEmail] = useState<string | null>(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [isExecLocked, setIsExecLocked] = useState(false)
  const [serverRole, setServerRole] = useState<Persona | null>(null)
  const [loading, setLoading] = useState(true)
  const [persona, setPersonaState] = useState<Persona | null>(null)

  // Fetch /api/me once on mount
  useEffect(() => {
    api
      .me()
      .then((data) => {
        setEmail(data.email)
        setIsAdmin(data.is_admin)
        setIsExecLocked(data.is_exec_locked)
        // Store the server-returned role (may be undefined on older backends)
        setServerRole(data.role ?? (data.is_admin ? 'admin' : 'pm'))
        setLoading(false)
      })
      .catch((err) => {
        console.error('Failed to fetch /api/me:', err)
        // Fall back to unauthenticated state rather than blocking the app
        setEmail(null)
        setIsAdmin(false)
        setIsExecLocked(false)
        setServerRole('pm')
        setLoading(false)
      })
  }, [])

  // Determine active persona once identity is known
  // PHASE A: persona = serverRole for non-admins; admins may keep their stored persona
  // (the full 'view as' UI comes in a later phase)
  useEffect(() => {
    if (loading || serverRole === null) return

    if (isExecLocked) {
      // Forced 'executive' — ignore localStorage
      setPersonaState('executive')
    } else if (isAdmin) {
      // Admins keep their stored persona (or default 'admin')
      // The 'view as' UI is a later phase; for now, admins just default to 'admin'
      const stored = loadStoredPersona()
      if (stored) {
        setPersonaState(stored)
      } else {
        setPersonaState('admin')
        storePersona('admin')
      }
    } else {
      // Non-admins: persona = serverRole (inferred, not self-selected)
      setPersonaState(serverRole)
    }
  }, [loading, isAdmin, isExecLocked, serverRole])

  const setPersona = useCallback(
    (newPersona: Persona) => {
      if (isExecLocked) {
        // Cannot switch when locked
        console.warn('Cannot switch persona: user is exec-locked')
        return
      }
      if (!isAdmin) {
        // Non-admins cannot switch persona (it's inferred from their role)
        console.warn('Cannot switch persona: non-admins have inferred persona')
        return
      }
      // Admins can switch (for now; full 'view as' comes later)
      setPersonaState(newPersona)
      storePersona(newPersona)
    },
    [isExecLocked, isAdmin],
  )

  // PHASE 4: coerce a stale/forced 'admin' persona down to 'pm' for a non-admin,
  // centrally, so Header nav filtering and App render/fallback read the SAME
  // trusted persona and cannot disagree. isAdmin comes from GET /api/me.
  const activePersona = effectivePersona(persona ?? 'pm', isAdmin)

  // PHASE C: isPreviewing = admin viewing as a non-admin persona (testing mode).
  // This helps Header show a clear "Viewing as X" badge when an admin is previewing.
  const isPreviewing = isAdmin && activePersona !== 'admin'

  const value = useMemo(
    () => ({
      email,
      isAdmin,
      isExecLocked,
      activePersona,
      setPersona,
      isPreviewing,
      loading,
    }),
    [email, isAdmin, isExecLocked, activePersona, setPersona, isPreviewing, loading],
  )

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>
}

export function useRole(): RoleContextValue {
  const context = useContext(RoleContext)
  if (!context) throw new Error('useRole must be used inside RoleProvider')
  return context
}

// Convenience hook for just the persona
export function usePersona(): Persona {
  return useRole().activePersona
}

/**
 * PHASE 3 — EXECUTIVE READ-ONLY: centralized read-only signal.
 *
 * Returns true when the given persona should see a read-only UI (no mutating
 * affordances: create/edit/delete/generate buttons, inline editors, status changes).
 * Executives are view-only consumers of value & roadmap data.
 */
export function readOnlyForPersona(persona: Persona): boolean {
  return persona === 'executive'
}

/**
 * PHASE 3 — EXECUTIVE READ-ONLY: convenience hook.
 *
 * Returns true when the active persona should see a read-only UI.
 * Views use this to hide mutating controls (buttons, editors, etc).
 */
export function useReadOnly(): boolean {
  return readOnlyForPersona(usePersona())
}
