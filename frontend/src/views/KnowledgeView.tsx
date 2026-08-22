// The knowledge base: browse, search, read, edit.
//
// Ported from `console.js:2130-2500` (`viewKnowledge`, `viewKbArticle`,
// `kbRenderMeta`, `kbEdit`, `kbCreate`) — the largest single view unit in the
// console at ~450 lines.
//
// THREE MODES, ONE DESTINATION
// ---------------------------
// The console kept browse / read / edit in one view and said why: "it is one task —
// you land, you look for something, you read it — and separate views would lose your
// place at every step." That is preserved. `mode` switches what is rendered, but the
// search term, the folder filter and the scroll position of the list all survive a
// trip into an article and back, because the list is not unmounted state — it is a
// react-query cache entry keyed on the filters.
//
// WHAT IS ADDRESSABLE, AND WHAT IS NOT
// ------------------------------------
// Only the article slug. See `lib/kbroute.ts` for the argument; the short version is
// that an article is a citation people paste into Slack, and a search term is not.
// This is the SPA's one deviation from no-router and it is scoped to this view.
//
// Endpoints: GET /kb/tree, GET /kb/articles, POST /kb/articles.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, FolderTree, Plus, Search } from 'lucide-react'

import { api } from '../api'
import { Banner, QueryState } from '../components/Banner'
import { StatStrip } from '../components/StatStrip'
import { useApiErrorToast } from '../components/Toasts'
import { clearArticle, pushArticle, slugFromLocation } from '../lib/kbroute'
import { NO_RETRY } from '../lib/retry'
import type { KbArticle } from '../types'
import { ArticleEditor } from './ArticleEditor'
import { ArticleView } from './ArticleView'

/** Which of the three modes is on screen. */
type Mode =
  | { name: 'list' }
  | { name: 'article'; slug: string }
  | { name: 'edit'; slug: string }

/**
 * The `<<match>>`-marked excerpt the search returns, split for highlighting.
 *
 * `ts_headline` is configured with `StartSel=<<,StopSel=>>` server-side
 * (`build_search_sql`) precisely so the markers survive HTML escaping — the console
 * relied on that to re-introduce `<mark>` into an escaped string. Here nothing is
 * escaped or re-introduced: the string is split on the markers and the pieces become
 * React children, so an article body containing a literal `<<` cannot become markup.
 */
export function splitExcerpt(excerpt: string): { text: string; match: boolean }[] {
  return excerpt
    .split(/(<<[^>]*?>>)/g)
    .filter((piece) => piece !== '')
    .map((piece) =>
      piece.startsWith('<<') && piece.endsWith('>>')
        ? { text: piece.slice(2, -2), match: true }
        : { text: piece, match: false },
    )
}

function Excerpt({ article }: { article: KbArticle }) {
  if (article.excerpt) {
    return (
      <div className="mt-1 text-xs text-navy-400">
        {splitExcerpt(article.excerpt).map((piece, index) =>
          piece.match ? (
            <mark key={index} className="rounded bg-lava/25 px-0.5 text-lava-300">
              {piece.text}
            </mark>
          ) : (
            <span key={index}>{piece.text}</span>
          ),
        )}
      </div>
    )
  }
  return article.summary ? (
    <div className="mt-1 text-xs text-navy-400">{article.summary}</div>
  ) : null
}

export default function KnowledgeView() {
  const queryClient = useQueryClient()
  const reportError = useApiErrorToast()

  // The deep link is read ONCE, in the initializer — not in an effect. An effect
  // would render the index first and then swap, so a pasted article link would
  // flash the KB list before showing what was asked for.
  const [mode, setMode] = useState<Mode>(() => {
    const slug = slugFromLocation()
    return slug ? { name: 'article', slug } : { name: 'list' }
  })

  // `query` is what has been SUBMITTED; `draft` is what is in the box. Separate so
  // typing does not fire a request per keystroke against a full-text search.
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [folderPath, setFolderPath] = useState<string | null>(null)

  const open = (slug: string) => {
    pushArticle(slug)
    setMode({ name: 'article', slug })
  }

  const backToList = () => {
    clearArticle()
    setMode({ name: 'list' })
  }

  // Back/forward between articles. The ONLY history subscription in the app, and it
  // reads nothing but the KB path: any other path resolves to `null` and lands on
  // the list, which is also the right answer for Back out of the last article.
  useEffect(() => {
    const onPop = () => {
      const slug = slugFromLocation()
      setMode(slug ? { name: 'article', slug } : { name: 'list' })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const tree = useQuery({ queryKey: ['kb-tree'], queryFn: api.kbTree })
  const articles = useQuery({
    queryKey: ['kb-articles', query, folderPath],
    queryFn: () => api.kbArticles({ q: query || null, folder_path: folderPath, limit: 50 }),
    // A rejected search expression is a 422 the user caused, and the server's reply
    // already says what to do about it ("Try plain words, or quote a phrase").
    // Retrying it three times just delays that sentence.
    retry: NO_RETRY.retry,
  })

  const create = useMutation({
    mutationFn: (title: string) => api.createKbArticle({ title, body_md: '', status: 'draft' }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['kb-articles'] })
      queryClient.invalidateQueries({ queryKey: ['kb-tree'] })
      // Straight into the editor: a new article is empty, and the only useful next
      // step is writing it. The console prompted for a title with `window.prompt`
      // and then showed the read view of a blank page.
      pushArticle(created.slug)
      setMode({ name: 'edit', slug: created.slug })
    },
    onError: (error) => reportError(error, 'Could not create the article.'),
  })

  if (mode.name === 'article') {
    return (
      <ArticleView
        slug={mode.slug}
        onBack={backToList}
        onEdit={() => setMode({ name: 'edit', slug: mode.slug })}
        onOpenSlug={open}
      />
    )
  }

  if (mode.name === 'edit') {
    return (
      <ArticleEditor
        slug={mode.slug}
        onDone={() => setMode({ name: 'article', slug: mode.slug })}
      />
    )
  }

  const totals = tree.data?.totals ?? {}
  const folders = tree.data?.folders ?? []
  const items = articles.data?.items ?? []

  const submit = () => setQuery(draft.trim())

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-lava" />
          <h2 className="font-bold text-white">Knowledge base</h2>
        </div>
        <p className="mt-1 max-w-[80ch] text-sm text-navy-400">
          The reasoning behind the portfolio — standards, proposals, studies and runbooks,
          attached to the use cases and sources they explain.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-navy-500" />
          <input
            className="input-field w-full pl-8"
            placeholder="Search titles and content…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submit()
            }}
          />
        </div>
        <button className="btn-secondary text-sm" onClick={submit}>
          Search
        </button>
        <button
          className="btn-primary text-sm"
          disabled={create.isPending}
          onClick={() => {
            // Untitled, then straight into the editor where the title is the first
            // field. `window.prompt` (the console's approach) cannot be styled, is
            // blocked in some embeddings, and asks for the title in a context where
            // you cannot see the article you are naming.
            create.mutate('Untitled article')
          }}
        >
          <Plus className="h-4 w-4" />
          {create.isPending ? 'Creating…' : 'New article'}
        </button>
      </div>

      {query ? (
        <div className="text-xs text-navy-400">
          Results for <span className="text-navy-200">{query}</span> ·{' '}
          <button
            className="text-lava-300 underline decoration-navy-600"
            onClick={() => {
              setDraft('')
              setQuery('')
            }}
          >
            clear
          </button>
        </div>
      ) : null}

      <StatStrip
        stats={[
          { label: 'Articles', value: totals.articles ?? 0 },
          { label: 'Published', value: totals.published ?? 0 },
          { label: 'Generated', value: totals.generated ?? 0 },
          { label: 'Unfiled', value: tree.data?.unfiled_count ?? 0 },
        ]}
      />

      {folders.length ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <FolderTree className="mr-0.5 h-4 w-4 text-navy-500" />
          <button
            className={folderPath == null ? 'btn-primary text-xs' : 'btn-secondary text-xs'}
            onClick={() => setFolderPath(null)}
          >
            All
          </button>
          {folders.map((folder) => (
            <button
              key={folder.id}
              className={
                folderPath === folder.path ? 'btn-primary text-xs' : 'btn-secondary text-xs'
              }
              onClick={() => setFolderPath(folder.path)}
              // The path, not the name: filtering is by subtree, so a nested
              // folder's own path is what the filter means.
              title={folder.path}
            >
              {folder.name} ({folder.article_count ?? 0})
            </button>
          ))}
        </div>
      ) : null}

      <QueryState
        query={articles}
        loading="Loading articles…"
        empty={items.length === 0}
        emptyMessage={
          query
            ? `Nothing matched "${query}".`
            : folderPath
              ? 'Nothing filed in this folder yet.'
              : 'No articles yet. Create one, or generate a proposal for a use case.'
        }
      />

      {items.length ? (
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-700 text-left text-xs uppercase tracking-wide text-navy-500">
                <th className="px-4 py-2.5 font-semibold">Title</th>
                <th className="px-4 py-2.5 font-semibold">Folder</th>
                <th className="px-4 py-2.5 font-semibold">Status</th>
                <th className="px-4 py-2.5 text-right font-semibold">Attached</th>
                <th className="px-4 py-2.5 font-semibold">Updated</th>
              </tr>
            </thead>
            <tbody>
              {items.map((article) => (
                <tr
                  key={article.slug}
                  className="border-b border-navy-800 last:border-0 hover:bg-navy-800/40"
                >
                  <td className="px-4 py-2.5 align-top">
                    {/* A real href so the link is copyable straight out of the
                        list — that is what the deep-link exception is for. */}
                    <a
                      href={`/kb/${article.slug}`}
                      className="font-medium text-lava-300 hover:underline"
                      onClick={(event) => {
                        if (event.metaKey || event.ctrlKey || event.shiftKey) return
                        event.preventDefault()
                        open(article.slug)
                      }}
                    >
                      {article.title}
                    </a>
                    {article.generated_by ? (
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-navy-500">
                        generated
                      </span>
                    ) : null}
                    <Excerpt article={article} />
                  </td>
                  <td className="px-4 py-2.5 align-top text-xs text-navy-400">
                    {article.folder_name || 'unfiled'}
                  </td>
                  <td className="px-4 py-2.5 align-top text-xs text-navy-300">{article.status}</td>
                  <td className="px-4 py-2.5 align-top text-right text-xs text-navy-400">
                    {article.link_count ?? 0} link · {article.attachment_count ?? 0} file
                  </td>
                  <td className="px-4 py-2.5 align-top text-xs text-navy-400">
                    {(article.updated_at ?? '').slice(0, 10) || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {tree.isError ? (
        <Banner kind="warn">
          The folder tree could not be loaded, so the filters above are missing. The article
          list is unaffected.
        </Banner>
      ) : null}
    </div>
  )
}
