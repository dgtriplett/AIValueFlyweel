// One article: the text, what it is attached to, its documents, and its history.
//
// Ported from `console.js:2272-2465` (`viewKbArticle`, `kbRenderMeta`).
//
// WHY THE METADATA IS NOT AN AFTERTHOUGHT
// --------------------------------------
// The console put four sections under the body and each answers a question the
// article itself cannot. "Attached to" is the load-bearing one: attaching an article
// to a use case is what makes it appear when someone opens that use case, so an
// unattached article is invisible outside the KB. The console said so in its empty
// state and that sentence is kept.
//
// WRITES HERE ARE NOT CONFIRM-GATED, DELIBERATELY
// ----------------------------------------------
// `server/routes/knowledge.py:24-36` argues this at length: gating "a person typing
// into a document" would add a dialog to every save and teach people to click
// through confirmations where the gate actually matters. Versioning is the
// protection instead. So the destructive-looking actions here (archive, restore) use
// an inline confirm step rather than `<ConfirmCard>`, which is for TOKEN-gated agent
// writes and would misrepresent what is happening.
//
// Endpoints: GET /kb/articles/{slug}, PUT /kb/articles/{slug},
// DELETE /kb/articles/{slug}, POST /kb/articles/{slug}/restore/{n},
// POST /kb/articles/{slug}/links, DELETE /kb/links/{id},
// POST /kb/articles/{slug}/attachments, GET /kb/attachments/{id},
// DELETE /kb/attachments/{id}.

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Archive,
  Download,
  History,
  Link2,
  Paperclip,
  Pencil,
  Trash2,
} from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { FileDrop, formatBytes } from '../components/FileDrop'
import { Markdown } from '../components/Markdown'
import { useApiErrorToast, useToast } from '../components/Toasts'
import { saveBlob } from '../lib/download'
import { NO_RETRY } from '../lib/retry'
import type { KbArticle } from '../types'

/** The entity kinds `server/routes/knowledge.py` accepts, with readable labels. */
const ENTITY_TYPES: { value: string; label: string }[] = [
  { value: 'use_case', label: 'Use case' },
  { value: 'data_asset', label: 'Data source' },
  { value: 'data_domain', label: 'Data domain' },
  { value: 'lob', label: 'Line of business' },
  { value: 'roadmap_item', label: 'Roadmap item' },
  { value: 'funding_request', label: 'Funding request' },
]

/** Phrased as the sentence the link makes, which is how the console read. */
const RELATIONS: { value: string; label: string }[] = [
  { value: 'standard', label: 'is the standard for' },
  { value: 'explains', label: 'explains' },
  { value: 'proposal', label: 'is the proposal for' },
  { value: 'evidence', label: 'is evidence for' },
  { value: 'related', label: 'relates to' },
]

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="card">
      <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-navy-400">
        {icon}
        {title}
      </div>
      {children}
    </section>
  )
}

export function ArticleView({
  slug,
  onBack,
  onEdit,
  onOpenSlug,
}: {
  slug: string
  onBack: () => void
  onEdit: () => void
  onOpenSlug: (slug: string) => void
}) {
  const queryClient = useQueryClient()
  const { show } = useToast()
  const reportError = useApiErrorToast()

  const [entityType, setEntityType] = useState('use_case')
  const [relation, setRelation] = useState('standard')
  const [entityId, setEntityId] = useState('')
  const [confirming, setConfirming] = useState<'archive' | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)

  const article = useQuery({
    queryKey: ['kb-article', slug],
    queryFn: () => api.kbArticle(slug),
    // A 404 here is a dead deep link, not a transient failure — someone pasted a
    // slug that was archived or never existed. Retrying cannot change that.
    retry: NO_RETRY.retry,
  })

  // Every write invalidates the article AND the list: status, attachment counts and
  // link counts are all columns in the list projection, so a stale list would show
  // "0 file" next to an article that just gained one. No setTimeout anywhere — the
  // console re-called `viewKbArticle(slug)` by hand after each write.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['kb-article', slug] })
    queryClient.invalidateQueries({ queryKey: ['kb-articles'] })
    queryClient.invalidateQueries({ queryKey: ['kb-tree'] })
  }

  const data = article.data

  const setStatus = useMutation({
    mutationFn: (status: string) => api.updateKbArticle(slug, { status }),
    onSuccess: (updated) => {
      refresh()
      show({ kind: 'info', message: `Moved to ${updated.status}.` })
    },
    onError: (error) => reportError(error, 'Could not change the status.'),
  })

  const archive = useMutation({
    mutationFn: () => api.deleteKbArticle(slug, false),
    onSuccess: () => {
      refresh()
      show({ kind: 'info', message: 'Article archived. It stays in the record.' })
      onBack()
    },
    onError: (error) => reportError(error, 'Could not archive the article.'),
  })

  const restore = useMutation({
    mutationFn: (version: number) => api.restoreKbVersion(slug, version),
    onSuccess: (updated) => {
      refresh()
      setRestoring(null)
      show({ kind: 'info', message: `Restored. The article is now v${updated.version}.` })
    },
    onError: (error) => reportError(error, 'Could not restore that version.'),
  })

  const addLink = useMutation({
    mutationFn: () =>
      api.createKbLink(slug, {
        entity_type: entityType,
        entity_id: Number(entityId),
        relation,
      }),
    onSuccess: () => {
      refresh()
      setEntityId('')
    },
    onError: (error) => reportError(error, 'Could not attach the article.'),
  })

  const removeLink = useMutation({
    mutationFn: (linkId: number) => api.deleteKbLink(linkId),
    onSuccess: refresh,
    onError: (error) => reportError(error, 'Could not detach that link.'),
  })

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadKbAttachment(slug, file),
    onSuccess: (result) => {
      refresh()
      show({
        kind: 'info',
        message: result.already_existed
          ? 'That exact file is already attached.'
          : `${result.filename} attached.`,
      })
    },
    onError: (error) => reportError(error, 'Could not upload the file.'),
  })

  const download = useMutation({
    mutationFn: (attachment: { id: number; filename: string }) =>
      api.kbAttachmentBlob(attachment.id).then((blob) => ({ blob, name: attachment.filename })),
    // Saved to disk, never previewed: the route serves these
    // `Content-Disposition: attachment` with `nosniff` and `default-src 'none'`
    // because the bytes are user-supplied. A blob preview would throw that away.
    onSuccess: ({ blob, name }) => saveBlob(blob, name),
    onError: (error) => reportError(error, 'Could not download that file.'),
  })

  const removeAttachment = useMutation({
    mutationFn: (attachmentId: number) => api.deleteKbAttachment(attachmentId),
    onSuccess: refresh,
    onError: (error) => reportError(error, 'Could not remove that file.'),
  })

  const back = (
    <button className="btn-secondary text-sm" onClick={onBack}>
      <ArrowLeft className="h-4 w-4" />
      Knowledge base
    </button>
  )

  if (article.isLoading || article.isError || !data) {
    return (
      <div className="space-y-4">
        {back}
        <QueryState
          query={article}
          loading="Loading the article…"
          errorMessage="That article could not be loaded. The link may point at something archived or deleted."
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {back}
        <div className="flex-1" />
        <button className="btn-secondary text-sm" onClick={onEdit}>
          <Pencil className="h-4 w-4" />
          Edit
        </button>
        <button
          className="btn-secondary text-sm"
          disabled={setStatus.isPending}
          onClick={() => setStatus.mutate(data.status === 'published' ? 'draft' : 'published')}
        >
          {data.status === 'published' ? 'Move to draft' : 'Publish'}
        </button>
        <button
          className="btn-secondary text-sm"
          disabled={archive.isPending}
          onClick={() => setConfirming('archive')}
        >
          <Archive className="h-4 w-4" />
          Archive
        </button>
      </div>

      {confirming === 'archive' ? (
        <Banner kind="warn">
          <div className="space-y-2">
            <div>
              Archive this article? It stays in the record and keeps its history and links, but
              drops out of search.
            </div>
            <div className="flex gap-2">
              <button
                className="btn-primary text-xs"
                disabled={archive.isPending}
                onClick={() => archive.mutate()}
              >
                {archive.isPending ? 'Archiving…' : 'Archive it'}
              </button>
              <button className="btn-secondary text-xs" onClick={() => setConfirming(null)}>
                Keep it
              </button>
            </div>
          </div>
        </Banner>
      ) : null}

      <ArticleHeader article={data} />

      {data.generated_by ? (
        <Banner kind="info">
          Generated by the {data.generated_by}. Its figures come from the portfolio, but review
          it before sharing.
        </Banner>
      ) : null}

      <div className="card">
        <Markdown
          md={data.body_md}
          // The parser cannot know which slugs resolve — only the server does. Marking
          // the dead ones is what stops a link that looks live from sending the reader
          // to an error page.
          deadSlugs={
            new Set(
              (data.references ?? []).filter((ref) => !ref.exists).map((ref) => ref.slug),
            )
          }
          onOpenSlug={onOpenSlug}
        />
      </div>

      <Section icon={<Link2 className="h-3.5 w-3.5" />} title="Attached to">
        {(data.links ?? []).length === 0 ? (
          <p className="text-sm text-navy-400">
            Not attached to anything yet. Attaching an article to a use case is what makes it
            show up when someone opens that use case.
          </p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {(data.links ?? []).map((link) => (
                <tr key={link.id} className="border-b border-navy-800 last:border-0">
                  <td className="py-2 pr-3 text-navy-200">{link.relation}</td>
                  <td className="py-2 pr-3 text-xs text-navy-400">
                    {link.entity_type.replace(/_/g, ' ')}
                  </td>
                  <td className="py-2 pr-3 text-navy-200">{link.label || `#${link.entity_id}`}</td>
                  <td className="py-2 text-right">
                    <button
                      className="btn-secondary text-xs"
                      disabled={removeLink.isPending}
                      onClick={() => removeLink.mutate(link.id)}
                    >
                      Detach
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select
            className="input-field text-sm"
            value={entityType}
            onChange={(event) => setEntityType(event.target.value)}
            aria-label="What kind of thing to attach to"
          >
            {ENTITY_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
          <select
            className="input-field text-sm"
            value={relation}
            onChange={(event) => setRelation(event.target.value)}
            aria-label="How this article relates to it"
          >
            {RELATIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            className="input-field w-24 text-sm"
            type="number"
            min={1}
            placeholder="id"
            value={entityId}
            onChange={(event) => setEntityId(event.target.value)}
            aria-label="The id of the thing to attach to"
          />
          <button
            className="btn-primary text-sm"
            // Guarded on a positive id rather than throwing after the click, which
            // is what the console did ("Enter the id of the thing to attach to").
            disabled={addLink.isPending || !(Number(entityId) > 0)}
            onClick={() => addLink.mutate()}
          >
            {addLink.isPending ? 'Attaching…' : 'Attach'}
          </button>
        </div>
      </Section>

      <Section icon={<Paperclip className="h-3.5 w-3.5" />} title="Documents">
        {(data.attachments ?? []).length === 0 ? (
          <p className="mb-3 text-sm text-navy-400">No files attached.</p>
        ) : (
          <table className="mb-3 w-full text-sm">
            <tbody>
              {(data.attachments ?? []).map((file) => (
                <tr key={file.id} className="border-b border-navy-800 last:border-0">
                  <td className="py-2 pr-3">
                    {/*
                      A BUTTON, not an anchor. An `<a href="/api/kb/attachments/1">`
                      (what the console had) bypasses axios and therefore the account
                      header, and a missing header does not raise — the server falls
                      back to the default account, so the user silently downloads
                      another tenant's document. This fetches through `api` and hands
                      the blob to `saveBlob`.
                    */}
                    <button
                      className="text-left font-medium text-lava-300 hover:underline"
                      disabled={download.isPending}
                      onClick={() => download.mutate({ id: file.id, filename: file.filename })}
                    >
                      <Download className="mr-1 inline h-3.5 w-3.5" />
                      {file.filename}
                    </button>
                  </td>
                  <td className="py-2 pr-3 text-xs text-navy-400">
                    {formatBytes(file.size_bytes)}
                  </td>
                  <td className="py-2 pr-3 text-xs text-navy-500">{file.storage}</td>
                  <td className="py-2 text-right">
                    <button
                      className="btn-secondary text-xs"
                      disabled={removeAttachment.isPending}
                      onClick={() => removeAttachment.mutate(file.id)}
                    >
                      <Trash2 className="h-3 w-3" />
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <FileDrop onFile={(file) => upload.mutate(file)} busy={upload.isPending} />
      </Section>

      {(data.references ?? []).length ? (
        <Section icon={<Link2 className="h-3.5 w-3.5" />} title="References">
          <p className="flex flex-wrap gap-x-2 gap-y-1 text-sm">
            {(data.references ?? []).map((reference) =>
              reference.exists ? (
                <a
                  key={reference.slug}
                  href={`/kb/${reference.slug}`}
                  className="text-lava-300 hover:underline"
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey) return
                    event.preventDefault()
                    onOpenSlug(reference.slug)
                  }}
                >
                  {reference.title || reference.slug}
                </a>
              ) : (
                <span key={reference.slug} className="text-navy-500">
                  {reference.slug} — not written yet
                </span>
              ),
            )}
          </p>
        </Section>
      ) : null}

      {(data.versions ?? []).length ? (
        <Section icon={<History className="h-3.5 w-3.5" />} title="History">
          <table className="w-full text-sm">
            <tbody>
              {(data.versions ?? []).map((version) => (
                <tr key={version.version} className="border-b border-navy-800 last:border-0">
                  <td className="py-2 pr-3 font-mono text-xs text-navy-300">v{version.version}</td>
                  <td className="py-2 pr-3 text-xs text-navy-400">
                    {version.change_note || '—'}
                  </td>
                  <td className="py-2 pr-3 text-xs text-navy-500">{version.edited_by || ''}</td>
                  <td className="py-2 pr-3 text-xs text-navy-500">
                    {(version.edited_at ?? '').slice(0, 10)}
                  </td>
                  <td className="py-2 text-right">
                    {restoring === version.version ? (
                      <span className="inline-flex gap-1.5">
                        <button
                          className="btn-primary text-xs"
                          disabled={restore.isPending}
                          onClick={() => restore.mutate(version.version)}
                        >
                          {restore.isPending ? 'Restoring…' : 'Confirm'}
                        </button>
                        <button
                          className="btn-secondary text-xs"
                          onClick={() => setRestoring(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        className="btn-secondary text-xs"
                        onClick={() => setRestoring(version.version)}
                        // The reassurance is the server's own: the current text is
                        // snapshotted first, so a restore is itself undoable.
                        title="The current text is kept as a version, so this is undoable"
                      >
                        Restore
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}
    </div>
  )
}

function ArticleHeader({ article }: { article: KbArticle }) {
  const meta = [
    article.folder_path || 'unfiled',
    `v${article.version ?? 1}`,
    article.status,
    `updated ${(article.updated_at ?? '').slice(0, 10) || '—'}${
      article.updated_by ? ` by ${article.updated_by}` : ''
    }`,
  ]
  return (
    <div>
      <h2 className="text-xl font-bold text-white">{article.title}</h2>
      {article.summary ? (
        <p className="mt-1 max-w-[80ch] text-sm text-navy-300">{article.summary}</p>
      ) : null}
      <p className="mt-1.5 text-xs text-navy-500">
        {meta.join(' · ')}
        {(article.tags ?? []).length ? ` · ${(article.tags ?? []).join(', ')}` : ''}
      </p>
    </div>
  )
}
