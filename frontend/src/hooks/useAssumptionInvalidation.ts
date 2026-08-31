// The one definition of "what a value-assumption change re-quantifies".
//
// WHY THIS EXISTS
// ---------------
// Every hypothesized and calculated-realized figure in the app is a formula over
// the 34 value assumptions, so ONE change to them re-quantifies the whole
// portfolio — not just the two totals on the Value & Assumptions screen, but the
// use-case list and the blast radius too. `AssumptionsView.tsx` has always known
// this and invalidated all four keys after a manual edit.
//
// A research recalibration (`ResearchView.tsx`) applies the SAME kind of change
// through the confirm gate: `server/routes/research.py:execute_apply_research`
// rewrites `value_assumptions` and captures a snapshot, exactly as a manual PUT
// to `/value-assumptions/{key}` does. The two paths therefore MUST invalidate the
// identical set of keys, or a recalibration leaves the dashboard KPIs showing the
// pre-calibration dollar values while a manual edit refreshes them — a stale-KPI
// bug that only appears down the research path.
//
// Keeping the list in two components guarantees they drift. This hook is the
// single source of truth both consume, so "what an assumption change invalidates"
// is defined once and asserted once (`frontend/test/plumbing.test.ts`).

import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'

/**
 * The KPI query keys any value-assumption change re-quantifies.
 *
 * Exported as data so a test can assert both invalidation paths cover exactly this
 * set without re-running React. Order is not significant to react-query, but it is
 * kept stable so the assertion reads as a list rather than a set comparison.
 *
 *  - `assumptions`      the editable numbers themselves
 *  - `portfolio-value`  the two portfolio totals derived from them
 *  - `use-cases`        every use case's computed_value is a formula over them
 *  - `blast`            the blast radius weights each node by that same value
 */
export const ASSUMPTION_INVALIDATION_KEYS = [
  ['assumptions'],
  ['portfolio-value'],
  ['use-cases'],
  ['blast'],
] as const

/**
 * Returns a function that invalidates every KPI query an assumption change
 * touches. Both the manual editor and the research recalibration call it, so the
 * dashboard refreshes identically whichever path changed the numbers.
 */
export function useAssumptionInvalidation(): () => void {
  const queryClient = useQueryClient()
  return useCallback(() => {
    for (const queryKey of ASSUMPTION_INVALIDATION_KEYS) {
      // Spread to a mutable array: react-query types `queryKey` as `QueryKey`
      // (a mutable readonly-free array), and the shared constant is `readonly`.
      queryClient.invalidateQueries({ queryKey: [...queryKey] })
    }
  }, [queryClient])
}
