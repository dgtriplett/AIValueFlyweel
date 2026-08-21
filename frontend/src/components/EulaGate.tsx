// The terms-of-use gate that fronts the whole SPA.
//
// It renders nothing at all until the effect has read localStorage, so a user who
// already accepted never sees the modal flash on a reload. Acceptance is a single
// localStorage key rather than server state: the reference library ships with the
// app, so the acknowledgement belongs to the browser looking at it.

import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'

import { EULA_KEY } from '../constants'

export function EulaGate({ children }: { children?: ReactNode }) {
  const [accepted, setAccepted] = useState(true)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    setAccepted(localStorage.getItem(EULA_KEY) === 'true')
  }, [])

  if (accepted) return <>{children}</>

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/80" />
      <div className="relative card w-full max-w-lg animate-scale-in">
        <div className="flex items-center gap-2 mb-2">
          <ShieldCheck className="w-6 h-6 text-lava" />
          <h2 className="font-bold text-xl">AI Value Flywheel — Terms of Use</h2>
        </div>

        <div className="text-sm text-navy-300 space-y-2 max-h-[50vh] overflow-y-auto pr-1">
          <p>
            This application and its bundled <strong>reference library</strong> (curated Power
            &amp; Utilities use-case catalog, value-model templates, and benchmark ranges) are
            provided by Databricks Field Engineering for evaluation and internal planning.
          </p>
          <p>
            <strong>Reference content</strong> remains the intellectual property of Databricks
            and is delivered under license (see LICENSE / Delta Sharing terms). It is provided
            "as is" without warranty; value estimates are directional and must be validated
            against your own data and assumptions.
          </p>
          <p>
            Your instance data (the use cases, statuses, and values you enter or import) belongs
            to you and is stored in your own workspace's Lakebase database.
          </p>
          <p>
            The optional "Auto-populate from Databricks" feature runs <strong>read-only</strong>{' '}
            queries against your system tables only with your explicit per-use consent.
          </p>
          <p className="text-navy-500 text-xs">
            Powered by Databricks. By continuing you acknowledge these terms.
          </p>
        </div>

        <label
          htmlFor="eula-accept"
          className="flex items-center gap-2 text-sm text-navy-300 my-3 cursor-pointer"
        >
          <input
            id="eula-accept"
            name="eula-accept"
            aria-label="I have read and accept these terms"
            type="checkbox"
            checked={checked}
            onChange={(event) => setChecked(event.target.checked)}
          />
          I have read and accept these terms.
        </label>

        <div className="flex justify-end">
          <button
            className="btn-primary"
            disabled={!checked}
            onClick={() => {
              localStorage.setItem(EULA_KEY, 'true')
              setAccepted(true)
            }}
          >
            Accept &amp; continue
          </button>
        </div>
      </div>
    </div>
  )
}
