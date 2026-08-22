// Renders the token tree from `lib/markdown.ts` as React elements.
//
// The split matters: the parser is a pure function over strings (testable under
// node, no renderer needed) and this file is the only place that decides what a
// heading looks like. Nothing here touches `dangerouslySetInnerHTML` — every piece
// of article text arrives as a React text child, so it is escaped by construction.
// That is the property the console's string-building renderer had to argue for at
// every step; here it is structural.
//
// WIKI LINKS AND WHAT "DEAD" MEANS
// --------------------------------
// The parser cannot know which `[[targets]]` resolve — only the server does, via
// the `references` array on `GET /kb/articles/{slug}`. So the caller passes the set
// of slugs that DO NOT exist, and those render as muted text with a trailing `?`
// rather than as links. A link that looks live but lands on an error page is worse
// than one that visibly says "not written yet": the first reads as a broken app,
// the second as an invitation to write it.

import type { ReactNode } from 'react'

import { parseMarkdown, type Block, type Inline } from '../lib/markdown'

export interface MarkdownProps {
  md?: string | null
  /**
   * Slugs the server said do not resolve. Rendered as muted text, not links.
   * Omit and every wiki link renders live — correct only when references are
   * unknown, e.g. previewing a body that has not been saved yet.
   */
  deadSlugs?: Set<string>
  /** Open a wiki link. Omitted in the editor's preview, where there is nowhere to go. */
  onOpenSlug?: (slug: string) => void
}

function Spans({
  spans,
  deadSlugs,
  onOpenSlug,
}: {
  spans: Inline[]
  deadSlugs?: Set<string>
  onOpenSlug?: (slug: string) => void
}) {
  return (
    <>
      {spans.map((span, index) => {
        switch (span.kind) {
          case 'strong':
            return (
              <strong key={index} className="font-semibold text-white">
                {span.text}
              </strong>
            )
          case 'em':
            return (
              <em key={index} className="italic">
                {span.text}
              </em>
            )
          case 'code':
            return (
              <code
                key={index}
                className="rounded bg-navy-900 px-1 py-0.5 font-mono text-[12px] text-lava-300"
              >
                {span.text}
              </code>
            )
          case 'wiki': {
            const { slug, label } = span.target
            if (deadSlugs?.has(slug)) {
              return (
                <span
                  key={index}
                  className="text-navy-500"
                  title={`No article named "${slug}" yet`}
                >
                  {label}?
                </span>
              )
            }
            // A real anchor, so the href is visible on hover and copyable — the
            // whole point of KB deep links is that people paste them. The click is
            // intercepted so navigation stays in-app; ctrl/cmd-click and
            // middle-click fall through to the browser and open a new tab.
            return (
              <a
                key={index}
                href={`/kb/${slug}`}
                className="text-lava-300 underline decoration-navy-600 hover:decoration-lava-300"
                onClick={(event) => {
                  if (!onOpenSlug || event.metaKey || event.ctrlKey || event.shiftKey) return
                  event.preventDefault()
                  onOpenSlug(slug)
                }}
              >
                {label}
              </a>
            )
          }
          default:
            return <span key={index}>{span.text}</span>
        }
      })}
    </>
  )
}

const HEADING_CLASS: Record<1 | 2 | 3 | 4, string> = {
  1: 'text-xl font-bold text-white mt-4 mb-1.5',
  2: 'text-lg font-bold mt-4 mb-1.5',
  3: 'text-base font-semibold text-white mt-3 mb-1',
  4: 'text-sm font-semibold text-navy-200 mt-3 mb-1',
}

function BlockView({
  block,
  deadSlugs,
  onOpenSlug,
}: {
  block: Block
  deadSlugs?: Set<string>
  onOpenSlug?: (slug: string) => void
}): ReactNode {
  const inline = (spans: Inline[]) => (
    <Spans spans={spans} deadSlugs={deadSlugs} onOpenSlug={onOpenSlug} />
  )

  switch (block.kind) {
    case 'heading': {
      const Tag = (['h1', 'h2', 'h3', 'h4'] as const)[block.level - 1]
      // h2 takes the brand accent, matching how the funding brief renders — an
      // article's section headings are its scannable structure.
      return (
        <Tag
          className={HEADING_CLASS[block.level]}
          style={block.level === 2 ? { color: '#FF3621' } : undefined}
        >
          {inline(block.spans)}
        </Tag>
      )
    }
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul'
      return (
        <Tag
          className={`my-2 ml-5 space-y-1 text-sm ${
            block.ordered ? 'list-decimal' : 'list-disc'
          }`}
        >
          {block.items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </Tag>
      )
    }
    case 'quote':
      return (
        <blockquote className="my-2 border-l-2 border-navy-600 pl-3 text-sm italic text-navy-300">
          {inline(block.spans)}
        </blockquote>
      )
    case 'code':
      return (
        <pre className="my-2 overflow-x-auto rounded-md bg-navy-900 p-3 text-[12px] leading-relaxed">
          <code className="font-mono text-navy-200">{block.text}</code>
        </pre>
      )
    case 'rule':
      return <hr className="my-4 border-navy-700" />
    default:
      return <p className="my-2 text-sm leading-relaxed text-navy-200">{inline(block.spans)}</p>
  }
}

export function Markdown({ md, deadSlugs, onOpenSlug }: MarkdownProps) {
  const blocks = parseMarkdown(md ?? '')
  if (!blocks.length) {
    return <p className="text-sm italic text-navy-500">This article is empty.</p>
  }
  return (
    <div className="max-w-[80ch]">
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} deadSlugs={deadSlugs} onOpenSlug={onOpenSlug} />
      ))}
    </div>
  )
}
