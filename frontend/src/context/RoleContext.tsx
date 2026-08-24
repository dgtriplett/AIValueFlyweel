// Role + persona context for the persona-aware UI (Phase 1+).
//
// This is the LINCHPIN other phases build on. It fetches GET /api/me once on load
// and exposes:
//   * email: the platform-attributed identity (null when local/unauthenticated).
//   * isAdmin: whether the identity is on the GRID_ATLAS_ADMINS allowlist.
//   * isExecLocked: whether the user is forced into 'executive' persona (hardcoded
//     false in Phase 1; Phase 2+ reads from an admin-managed table).
//   * activePersona: the user's self-selected persona ('admin' | 'pm' | 'executive').
//   * setPersona: switch persona (disabled when isExecLocked).
//
// PERSONA DETERMINATION:
//   - If isExecLocked: forced 'executive' (cannot switch).
//   - Else: user's self-selected persona persisted in localStorage, defaulting to
//     'admin' if isAdmin, else 'pm'.
//
// Phase 1 establishes the context + the fetch + the persisted state. Phase 2+ makes
// the nav react to persona (hide/show views, adjust styling, etc).

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
        setLoading(false)
      })
      .catch((err) => {
        console.error('Failed to fetch /api/me:', err)
        // Fall back to unauthenticated state rather than blocking the app
        setEmail(null)
        setIsAdmin(false)
        setIsExecLocked(false)
        setLoading(false)
      })
  }, [])

  // Determine active persona once identity is known
  useEffect(() => {
    if (loading) return

    if (isExecLocked) {
      // Forced 'executive' — ignore localStorage
      setPersonaState('executive')
    } else {
      const stored = loadStoredPersona()
      if (stored) {
        setPersonaState(stored)
      } else {
        // Default: 'admin' if isAdmin, else 'pm'
        const defaultPersona: Persona = isAdmin ? 'admin' : 'pm'
        setPersonaState(defaultPersona)
        storePersona(defaultPersona)
      }
    }
  }, [loading, isAdmin, isExecLocked])

  const setPersona = useCallback(
    (newPersona: Persona) => {
      if (isExecLocked) {
        // Cannot switch when locked
        console.warn('Cannot switch persona: user is exec-locked')
        return
      }
      setPersonaState(newPersona)
      storePersona(newPersona)
    },
    [isExecLocked],
  )

  // PHASE 4: coerce a stale/forced 'admin' persona down to 'pm' for a non-admin,
  // centrally, so Header nav filtering and App render/fallback read the SAME
  // trusted persona and cannot disagree. isAdmin comes from GET /api/me.
  const activePersona = effectivePersona(persona ?? 'pm', isAdmin)

  const value = useMemo(
    () => ({
      email,
      isAdmin,
      isExecLocked,
      activePersona,
      setPersona,
      loading,
    }),
    [email, isAdmin, isExecLocked, activePersona, setPersona, loading],
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
