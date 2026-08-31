// Writing an article: title, summary, tags, body, and what changed.
//
// Ported from `console.js:2477-2530` (`kbEdit`, `kbSave`).
//
// WHY A LIVE PREVIEW WAS ADDED
// ---------------------------
// The console had a bare textarea, which is a problem specific to THIS body format:
// `[[wiki links]]` are the construct that makes the KB navigable, and they are the
// one thing an author cannot verify by eye — whether `[[Recloser Coordination]]`
// resolves depends on slugification rules on the server. The preview renders through
// the same parser the read view uses, so what you see is what will be saved.
//
// The preview cannot say whether a link RESOLVES, though, and it deliberately does
// not guess: `deadSlugs` is only known from the server's `references` array, which is
// computed on save. So every wiki link previews as live, and the read view is where
// dead ones get marked. Guessing here would show false "not written yet" markers on
// links that are fine.
//
// UNSAVED WORK IS GUARDED
// ----------------------
// Saving snapshots the previous version, so an edit is recoverable — but abandoning
// an unsaved edit is not, and this is a long-form text field. Cancel with a dirty
// body asks first.
//
// Endpoints: GET /kb/articles/{slug}, PUT /kb/articles/{slug}.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Eye, Save } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { Markdown } from '../components/Markdown'
import { useApiErrorToast, useToast } from '../components/Toasts'
import { NO_RETRY } from '../lib/retry'

/** The editable fields, as strings — `tags` is comma-separated in the input. */
interface Draft {
  title: string
  summary: string
  tags: string
  body_md: string
  change_note: string
}

/** `"protection, ansi c37"` -> `['protection', 'ansi c37']`. The server lowercases
 *  and de-duplicates, so this only has to split and trim. */
export function parseTags(value: string): string[] {
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
}

export function ArticleEditor({ slug, onDone }: { slug: string; onDone: () => void }) {
  const queryClient = useQueryClient()
  const { show } = useToast()
  const reportError = useApiErrorToast()

  const article = useQuery({
    queryKey: ['kb-article', slug],
    queryFn: () => api.kbArticle(slug),
    retry: NO_RETRY.retry,
  })

  const [draft, setDraft] = useState<Draft | null>(null)
  const [preview, setPreview] = useState(false)
  const [confirmDiscard, setConfirmDiscard] = useState(false)

  // Seeded from the fetch exactly once. Keyed on `slug` rather than on the query
  // data, so a background refetch — or the invalidation the save itself triggers —
  // cannot overwrite what is being typed. `null` until the article lands.
  useEffect(() => {
    setDraft(null)
    setPreview(false)
    setConfirmDiscard(false)
  }, [slug])

  useEffect(() => {
    if (draft != null || !article.data) return
    setDraft({
      title: article.data.title,
      summary: article.data.summary ?? '',
      tags: (article.data.tags ?? []).join(', '),
      body_md: article.data.body_md ?? '',
      change_note: '',
    })
  }, [article.data, draft])

  const save = useMutation({
    mutationFn: (next: Draft) =>
      api.updateKbArticle(slug, {
        title: next.title.trim(),
        summary: next.summary.trim(),
        tags: parseTags(next.tags),
        body_md: next.body_md,
        change_note: next.change_note.trim() || null,
      }),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['kb-article', slug] })
      queryClient.invalidateQueries({ queryKey: ['kb-articles'] })
      queryClient.invalidateQueries({ queryKey: ['kb-tree'] })
      show({
        kind: 'info',
        // `content_changed` is the server's own answer to "did this make a version",
        // and it is worth saying: re-tagging deliberately does not, and someone
        // looking for their edit in the history needs to know which happened.
        message: updated.content_changed
          ? `Saved as v${updated.version}. The previous version is kept.`
          : 'Saved. No new version — the text did not change.',
      })
      onDone()
    },
    onError: (error) => reportError(error, 'Could not save the article.'),
  })

  const original = article.data
  const dirty =
    draft != null &&
    original != null &&
    (draft.title !== original.title ||
      draft.summary !== (original.summary ?? '') ||
      draft.tags !== (original.tags ?? []).join(', ') ||
      draft.body_md !== (original.body_md ?? ''))

  const leave = () => {
    if (dirty) {
      setConfirmDiscard(true)
      return
    }
    onDone()
  }

  if (!draft) {
    return (
      <div className="space-y-4">
        <button className="btn-secondary text-sm" onClick={onDone}>
          <ArrowLeft className="h-4 w-4" />
          Back
        </button>
        <QueryState query={article} loading="Loading the article…" />
      </div>
    )
  }

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-secondary text-sm" onClick={leave}>
          <ArrowLeft className="h-4 w-4" />
          Cancel
        </button>
        <div className="flex-1" />
        <button
          className={preview ? 'btn-primary text-sm' : 'btn-secondary text-sm'}
          onClick={() => setPreview((on) => !on)}
        >
          <Eye className="h-4 w-4" />
          {preview ? 'Hide preview' : 'Preview'}
        </button>
        <button
          className="btn-primary text-sm"
          disabled={save.isPending || !draft.title.trim()}
          onClick={() => save.mutate(draft)}
        >
          <Save className="h-4 w-4" />
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>

      {confirmDiscard ? (
        <Banner kind="warn">
          <div className="space-y-2">
            <div>
              Discard these edits? Saving keeps the previous version, but unsaved text is not
              recoverable.
            </div>
            <div className="flex gap-2">
              <button className="btn-primary text-xs" onClick={onDone}>
                Discard them
              </button>
              <button
                className="btn-secondary text-xs"
                onClick={() => setConfirmDiscard(false)}
              >
                Keep editing
              </button>
            </div>
          </div>
        </Banner>
      ) : null}

      <div>
        <h2 className="text-lg font-bold text-white">Editing: {original?.title}</h2>
        <p className="mt-1 max-w-[80ch] text-sm text-navy-400">
          Markdown. Link to another article with{' '}
          <code className="rounded bg-navy-900 px-1 py-0.5 font-mono text-[12px] text-lava-300">
            [[Its Title]]
          </code>
          . Saving keeps the previous version, so an edit is always recoverable.
        </p>
      </div>

      <div className="card space-y-3">
        <Field label="Title" htmlFor="kb-title">
          <input
            id="kb-title"
            className="input-field w-full"
            value={draft.title}
            onChange={(event) => set('title', event.target.value)}
          />
          {!draft.title.trim() ? (
            <p className="mt-1 text-xs text-lava-300">A title is required.</p>
          ) : null}
        </Field>

        <Field label="Summary — one line, shown in search results" htmlFor="kb-summary">
          <input
            id="kb-summary"
            className="input-field w-full"
            value={draft.summary}
            onChange={(event) => set('summary', event.target.value)}
          />
        </Field>

        <Field label="Tags, comma separated" htmlFor="kb-tags">
          <input
            id="kb-tags"
            className="input-field w-full"
            value={draft.tags}
            onChange={(event) => set('tags', event.target.value)}
            placeholder="protection, ansi c37"
          />
        </Field>
      </div>

      <div className={preview ? 'grid grid-cols-1 gap-4 lg:grid-cols-2' : ''}>
        <div className="card">
          <Field label="Body" htmlFor="kb-body">
            <textarea
              id="kb-body"
              className="input-field w-full font-mono text-[13px] leading-relaxed"
              rows={preview ? 24 : 22}
              value={draft.body_md}
              onChange={(event) => set('body_md', event.target.value)}
            />
          </Field>
        </div>

        {preview ? (
          <div className="card">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-navy-400">
              Preview
            </div>
            {/*
              No `deadSlugs` and no `onOpenSlug`: whether a `[[link]]` resolves is the
              server's answer (the `references` array, computed on read), and there is
              nowhere to navigate from inside an unsaved edit.
            */}
            <Markdown md={draft.body_md} />
          </div>
        ) : null}
      </div>

      <div className="card">
        <Field label="What changed — optional, shown in the history" htmlFor="kb-note">
          <input
            id="kb-note"
            className="input-field w-full"
            placeholder="e.g. clarified the recloser coordination rule"
            value={draft.change_note}
            onChange={(event) => set('change_note', event.target.value)}
          />
        </Field>
      </div>
    </div>
  )
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-xs uppercase tracking-wide text-navy-400">
        {label}
      </label>
      {children}
    </div>
  )
}
