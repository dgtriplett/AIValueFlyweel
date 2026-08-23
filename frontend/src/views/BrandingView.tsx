// Branding — show the customer's name and logo in the header.
//
// Ported from `console.js:1861-1933` (`subBranding`). It is a small feature, but
// it is what makes the app read as the customer's during a workshop rather than as
// a generic tool. The name falls back to the researched company profile, so an
// instance that has run company research is already branded without anyone setting
// anything — which is why the field's placeholder shows the resolved name and its
// `value` is blank unless the source is a custom override.
//
// TWO THINGS THE PORT DELIBERATELY CHANGES FROM THE CONSOLE
// ---------------------------------------------------------
//  - The logo upload was `<input type="file">` + an Upload button; here it is the
//    shared `<FileDrop>` with a custom `validate` enforcing the server's rules
//    (≤2MB, image MIME only — `server/routes/branding.py:MAX_LOGO_BYTES` /
//    `ALLOWED_MIME`). The check is a COURTESY, not a gate: the server sniffs magic
//    bytes and remains the authority, and its message is shown when it rejects.
//  - The current-logo <img> keeps the `?t=` cache-buster the console used, because
//    the endpoint sets a 60s cache and a replaced logo must show immediately.
//    Every read/write goes through `http` (the account interceptor), and the <img>
//    src is the ONE place the bytes are fetched by the browser rather than axios —
//    that is unavoidable for an <img>, and the endpoint is not account-leaking in
//    the §4.1 sense because it 404s without a logo and the header shows the same
//    per-account logo the app already renders.

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Image as ImageIcon, Trash2 } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { FileDrop, formatBytes } from '../components/FileDrop'
import { useApiErrorToast } from '../components/Toasts'
import { NO_RETRY } from '../lib/retry'

/** `server/routes/branding.py:MAX_LOGO_BYTES` — a header logo, not a photo. */
export const MAX_LOGO_BYTES = 2 * 1024 * 1024

/** The MIME types `ALLOWED_MIME` accepts, as file extensions for the picker. */
export const LOGO_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'] as const

/**
 * Why this logo cannot be uploaded, in words, or `null` if it can.
 *
 * Duplicates the server's cap and extension list narrowly — only the obvious
 * mistakes, so a mis-picked 5MB photo does not cost a round trip and a 413. The
 * server still validates magic bytes and its message is authoritative.
 */
export function logoRejection(file: File): string | null {
  if (file.size === 0) return `${file.name} is empty.`
  if (file.size > MAX_LOGO_BYTES) {
    return (
      `${file.name} is ${formatBytes(file.size)}; the limit is 2 MB. ` +
      'A header logo should be well under that — try exporting it smaller.'
    )
  }
  const lowered = file.name.toLowerCase()
  if (!LOGO_EXTENSIONS.some((extension) => lowered.endsWith(extension))) {
    return `${file.name} is not an accepted image type. Accepted: PNG, JPEG, GIF, WebP, SVG.`
  }
  return null
}

export function BrandingView(): JSX.Element {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  const brandingQuery = useQuery({ queryKey: ['branding'], queryFn: api.branding })
  const branding = brandingQuery.data

  // The form is uncontrolled-ish: seeded from the query once loaded, then owned
  // locally so typing does not fight a refetch. `useMemo` keys the initial values
  // to the loaded data so a fresh load (e.g. after a switch) reseeds them.
  const initial = useMemo(
    () => ({
      // A custom name is the override; a company/default name is the placeholder.
      name: branding?.source === 'custom' ? (branding?.display_name ?? '') : '',
      subtitle: branding?.subtitle ?? '',
      accent: branding?.accent_color ?? '#FF3621',
    }),
    [branding],
  )
  const [name, setName] = useState<string | null>(null)
  const [subtitle, setSubtitle] = useState<string | null>(null)
  const [accent, setAccent] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const nameValue = name ?? initial.name
  const subtitleValue = subtitle ?? initial.subtitle
  const accentValue = accent ?? initial.accent

  const invalidate = () => {
    // Branding drives the header; invalidate it so the wordmark reflects the change.
    queryClient.invalidateQueries({ queryKey: ['branding'] })
  }

  const save = useMutation({
    mutationFn: () =>
      api.updateBranding({
        display_name: nameValue.trim() || null,
        subtitle: subtitleValue.trim() || null,
        accent_color: accentValue,
      }),
    onSuccess: () => {
      invalidate()
      setSaved('Saved.')
    },
    onError: (error) => {
      setSaved(null)
      reportError(error, 'Could not save the branding.')
    },
  })

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadBrandingLogo(file),
    // A logo upload is not idempotent-safe to replay on an ambiguous failure.
    ...NO_RETRY,
    onSuccess: (result) => {
      invalidate()
      setSaved(`Uploaded ${Math.round(result.bytes / 1024)}KB.`)
    },
    onError: (error) => reportError(error, 'Could not upload the logo.'),
  })

  const removeLogo = useMutation({
    mutationFn: () => api.deleteBrandingLogo(),
    onSuccess: () => {
      invalidate()
      setSaved('Logo removed.')
    },
    onError: (error) => reportError(error, 'Could not remove the logo.'),
  })

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <ImageIcon className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Branding</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1 max-w-[78ch]">
          Show the customer&apos;s name and logo in the header, so the app reads as theirs in a
          workshop. The name defaults to the researched company when one exists.
        </p>
      </div>

      <QueryState query={brandingQuery} loading="Loading branding…" />

      {saved ? <Banner kind="ok">{saved}</Banner> : null}

      {branding ? (
        <>
          <div className="card space-y-3">
            <h3 className="font-semibold text-white">Header</h3>
            <div>
              <label className="block text-sm text-navy-300" htmlFor="b-name">
                Display name{' '}
                <span className="text-navy-500">(from {branding.source})</span>
              </label>
              <input
                id="b-name"
                className="mt-1 w-full rounded-md border border-navy-600 bg-navy-900 px-3 py-2 text-sm text-white"
                placeholder={branding.display_name}
                value={nameValue}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[220px]">
                <label className="block text-sm text-navy-300" htmlFor="b-sub">
                  Subtitle
                </label>
                <input
                  id="b-sub"
                  className="mt-1 w-full rounded-md border border-navy-600 bg-navy-900 px-3 py-2 text-sm text-white"
                  value={subtitleValue}
                  onChange={(event) => setSubtitle(event.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-navy-300" htmlFor="b-accent">
                  Accent
                </label>
                <input
                  id="b-accent"
                  type="color"
                  className="mt-1 h-10 w-14 rounded-md border border-navy-600 bg-navy-900 p-1"
                  value={accentValue}
                  onChange={(event) => setAccent(event.target.value)}
                />
              </div>
              <button
                className="btn-primary text-sm"
                disabled={save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="font-semibold text-white">Logo</h3>
            {branding.has_logo && branding.logo_url ? (
              <div className="flex items-center gap-3">
                <img
                  // `?t=` cache-buster: the endpoint caches for 60s, but a replaced
                  // logo must show immediately after an upload during a workshop.
                  src={`${branding.logo_url}?t=${branding.updated_at ?? Date.now()}`}
                  alt="Current logo"
                  className="max-h-11 rounded bg-navy-900 p-1.5"
                />
                <button
                  className="btn-secondary text-sm flex items-center gap-1.5"
                  disabled={removeLogo.isPending}
                  onClick={() => removeLogo.mutate()}
                >
                  <Trash2 className="w-4 h-4" />
                  {removeLogo.isPending ? 'Removing…' : 'Remove'}
                </button>
              </div>
            ) : (
              <p className="text-sm text-navy-400">No logo uploaded.</p>
            )}

            <FileDrop
              onFile={(file) => upload.mutate(file)}
              busy={upload.isPending}
              busyLabel="Uploading…"
              label="Drop a logo here, or click to choose"
              hint="PNG, JPEG, GIF, WebP or SVG · max 2 MB"
              accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml"
              validate={logoRejection}
            />
          </div>
        </>
      ) : null}
    </div>
  )
}

export default BrandingView
