// A drop zone / file picker that rejects what the server would reject, first.
//
// The console's version was `<input type="file">` plus an Upload button
// (`console.js:2400-2404`), which meant every mistake cost a round trip: pick a
// 40MB interconnection study, wait for it to upload, then read a 422. The size and
// extension rules are known to the client, so the obvious ones are answered
// immediately.
//
// The rules are the KB attachment ones BY DEFAULT, not by construction: the caps
// differ per endpoint (attachments 25 MB, discovery CSVs 64 MB), so callers pass a
// custom `validate` (typically wrapping `rejectionOf` with the right `FileLimits`)
// to override them. Defaulting rather than requiring it keeps the checks honest —
// a drop zone that silently used the wrong cap would reject files the server would
// have accepted.
//
// WHAT THIS DOES *NOT* DO
// -----------------------
// It does not decide whether an upload is safe. `server/knowledge.py`
// `validate_attachment` sniffs MAGIC BYTES and requires the declared type to be a
// plausible reading of them, because a filename and a browser-supplied
// Content-Type are both attacker-controlled. Nothing on this side can be trusted
// with that, so these checks are a COURTESY to the user, not a gate — the server
// still rejects, and the caller still surfaces its message.

import { useRef, useState } from 'react'
import { Paperclip, Upload } from 'lucide-react'

/** `server/knowledge.py:115` — `MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024`. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

/**
 * The extensions `ALLOWED_ATTACHMENT_TYPES` accepts, flattened.
 *
 * Extensions rather than MIME types because this feeds the `accept` attribute and
 * the user-facing message, and because the server checks the extension against the
 * declared type anyway. Kept in the server's order of significance.
 */
export const ACCEPTED_EXTENSIONS = [
  '.pdf',
  '.docx',
  '.xlsx',
  '.pptx',
  '.doc',
  '.xls',
  '.ppt',
  '.csv',
  '.md',
  '.txt',
  '.png',
  '.jpg',
  '.jpeg',
] as const

/** `1.4 MB`, or `812 KB` under a megabyte. */
export function formatBytes(bytes?: number | null): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${Math.round(kb)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

/**
 * The rules a file is checked against, when they are not the KB attachment ones.
 *
 * Added in Tier 3 Phase 9, and the reason is a real mismatch rather than
 * generality for its own sake: the discovery CSV uploads accept 64 MB
 * (`server/routes/ingestion.py:53` — a 200k-table estate extracts to ~40 MB),
 * so an attachment-capped drop zone would reject valid files BEFORE the request,
 * which is worse than the round trip it was built to save. Both fields are
 * optional and default to the attachment rules, so every existing caller and
 * every existing assertion is unchanged.
 */
export interface FileLimits {
  /** Bytes. Defaults to `MAX_ATTACHMENT_BYTES`. */
  maxBytes?: number
  /** Lower-case, dot-prefixed. Defaults to `ACCEPTED_EXTENSIONS`. */
  extensions?: readonly string[]
}

/** `25 MB` / `64 MB` — whole megabytes, because that is how the caps are written. */
function capLabel(maxBytes: number): string {
  return `${Math.round(maxBytes / 1024 / 1024)} MB`
}

/**
 * Why this file cannot be uploaded, in words, or `null` if it can.
 *
 * The size message quotes the actual size and the cap, and repeats the server's
 * advice ("link to it in the article body instead") — a bare "too large" leaves
 * someone with a 40MB study and no idea what to do with it. That advice is only
 * appended for ATTACHMENTS: telling someone with an oversized `all_tables.csv` to
 * link to it in an article body would be confident nonsense, so a caller that
 * raised the cap gets the size sentence without it.
 */
export function rejectionOf(file: File, limits: FileLimits = {}): string | null {
  const maxBytes = limits.maxBytes ?? MAX_ATTACHMENT_BYTES
  const extensions = limits.extensions ?? ACCEPTED_EXTENSIONS
  if (file.size === 0) return `${file.name} is empty.`
  if (file.size > maxBytes) {
    return (
      `${file.name} is ${formatBytes(file.size)}; the limit is ${capLabel(maxBytes)}.` +
      (maxBytes === MAX_ATTACHMENT_BYTES
        ? ' Link to it in the article body instead of attaching it.'
        : '')
    )
  }
  const lowered = file.name.toLowerCase()
  if (!extensions.some((extension) => lowered.endsWith(extension))) {
    return `${file.name} is not an accepted file type. Accepted: ${extensions.join(', ')}.`
  }
  return null
}

export interface FileDropProps {
  onFile: (file: File) => void
  /** Disables the zone and dims it — set while an upload is in flight. */
  busy?: boolean
  busyLabel?: string
  label?: string
  hint?: string
  accept?: string
  validate?: (file: File) => string | null
}

export function FileDrop({
  onFile,
  busy = false,
  busyLabel = 'Uploading…',
  label = 'Drop a file here, or click to choose',
  hint = 'PDF, Word, Excel, PowerPoint, CSV, text, images. Up to 25 MB — enough for an interconnection study.',
  accept = ACCEPTED_EXTENSIONS.join(','),
  validate = rejectionOf,
}: FileDropProps) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [rejected, setRejected] = useState<string | null>(null)

  const offer = (file: File | null | undefined) => {
    if (!file) return
    const problem = validate(file)
    setRejected(problem)
    if (!problem) onFile(file)
  }

  return (
    <div>
      {/*
        A button, not a div with a click handler: this opens a file dialog, so it
        must be reachable by keyboard and announced as something actionable. The
        real input stays hidden — browsers do not let its own chrome be styled —
        and this delegates to it.
      */}
      <button
        type="button"
        disabled={busy}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          if (!busy) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          if (busy) return
          offer(event.dataTransfer.files?.[0])
        }}
        className={`flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-5 text-center transition-colors ${
          dragging ? 'border-lava bg-lava/5' : 'border-navy-600 hover:border-navy-500'
        } ${busy ? 'cursor-wait opacity-60' : 'cursor-pointer'}`}
      >
        {busy ? (
          <Upload className="h-5 w-5 animate-pulse text-lava" />
        ) : (
          <Paperclip className="h-5 w-5 text-navy-400" />
        )}
        <span className="text-sm text-navy-200">{busy ? busyLabel : label}</span>
        <span className="max-w-[60ch] text-xs text-navy-500">{hint}</span>
      </button>

      <input
        ref={input}
        type="file"
        className="hidden"
        accept={accept}
        onChange={(event) => {
          offer(event.target.files?.[0])
          // Cleared so picking the SAME file again still fires `change`. Without
          // this, a retry after a rejected upload looks like nothing happened.
          event.target.value = ''
        }}
      />

      {rejected ? <p className="mt-2 text-xs text-lava-300">{rejected}</p> : null}
    </div>
  )
}
