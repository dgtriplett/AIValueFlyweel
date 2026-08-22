// Markdown -> a tree of tokens, with no HTML anywhere.
//
// WHAT THIS REPLACES
// ------------------
// The console's `renderMarkdown` (`console.js:2532-2591`) produced an HTML STRING
// and assigned it to `innerHTML`. It was safe, but only by an argument that had to
// hold at every step: escape the whole source with `text()` FIRST, then
// re-introduce a fixed set of constructs, and never write user text into an
// attribute except a slug stripped to `[a-z0-9-]`. That ordering WAS the security
// property — one pattern added out of order, or one attribute interpolated
// directly, and article bodies (which come from users AND from the model) become
// an injection vector.
//
// It also carried a genuine liability: the fenced-code placeholder used literal NUL
// bytes (`\0BLOCK0\0`) as sentinels. NUL in a text file makes `grep` treat the
// whole file as binary and SILENTLY report no matches, so a plain `grep` for any KB
// symbol in `console.js` returned nothing — the source looked like it had no
// knowledge-base code at all. That cost real time in this migration.
//
// So this parses to DATA and the React layer renders it as elements. React escapes
// every text child by construction, which means the safety property no longer
// depends on the order of operations in a chain of `.replace()` calls: there is no
// HTML string to inject into. No sentinel is needed either — fenced blocks become
// their own block token.
//
// WHY NOT A MARKDOWN LIBRARY
// --------------------------
// `[[wiki links]]` are not markdown, and they are the construct that makes a set
// of KB articles navigable rather than a flat list. Any library needs a plugin for
// them plus a sanitizer for its HTML output, which is more dependency and more
// attack surface than this. The subset below is exactly what the console supported,
// which is what the existing corpus is written in.
//
// This file is `.ts`, not `.tsx`, and deliberately holds no JSX: it is a pure
// function over strings, so `tests/` can drive it under node without a renderer.
// `components/Markdown.tsx` renders what it returns.

/** The slug a `[[wiki link]]` target resolves to. */
export interface WikiTarget {
  slug: string
  /** What to show — the pipe label if given, else the raw target. */
  label: string
}

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'em'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'wiki'; target: WikiTarget }

export type Block =
  | { kind: 'heading'; level: 1 | 2 | 3 | 4; spans: Inline[] }
  | { kind: 'paragraph'; spans: Inline[] }
  | { kind: 'list'; ordered: boolean; items: Inline[][] }
  | { kind: 'quote'; spans: Inline[] }
  | { kind: 'code'; text: string; language: string | null }
  | { kind: 'rule' }

// Mirrors `server/knowledge.py`'s `_SLUG_STRIP` / `_SLUG_SPACES` and
// `MAX_SLUG_LENGTH`. Both sides MUST agree on what a title resolves to, or a
// `[[Recloser Coordination]]` written by an author points at a slug the server
// never generated and the link is permanently dead.
const SLUG_STRIP = /[^\w\s-]/g
const SLUG_SPACES = /[-\s]+/g
const MAX_SLUG_LENGTH = 80

/**
 * Slugify a wiki-link target the way the server slugifies a title.
 *
 * NOT a full reimplementation of `slugify()`: the server also does NFKD unicode
 * folding, a sha256 fallback for titles that slugify to nothing, reserved-slug
 * suffixing and uniqueness numbering. Those need state this side does not have
 * (the set of taken slugs) or produce a slug no author would type. What matters
 * here is that the common case — ASCII words and spaces — agrees exactly.
 *
 * Unicode is folded with `normalize('NFKD')` and a combining-mark strip, which
 * matches the server's encode-to-ascii-and-ignore for accented Latin ("Réseau" ->
 * "reseau"). A target that folds away to nothing returns `''`, and the caller
 * renders it as plain text rather than a link to `/kb/`.
 */
export function slugifyWikiTarget(target: string): string {
  // Escapes, not literal combining marks: a literal one is invisible in an editor
  // and the next person to touch this line cannot see what it matches.
  const folded = (target || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  return folded
    .replace(SLUG_STRIP, '')
    .trim()
    .toLowerCase()
    .replace(SLUG_SPACES, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/^-+|-+$/g, '')
}

// One alternation, so the scanner walks the string once and never re-scans text it
// has already emitted. Order matters only for `code`: backticked text must win, so
// `` `**not bold**` `` stays literal.
//
// `[[...]]` bounds the target at 120 chars and forbids `]` and newline inside,
// matching `extract_wiki_links` server-side. The closing `]]` is REQUIRED there for
// a stated reason — an unterminated `[[` used to swallow the rest of the paragraph
// — so it is required here too, or the two disagree about what is a link.
const INLINE_PATTERN = new RegExp(
  [
    '`([^`\\n]+)`',
    '\\[\\[([^\\]|\\n]{1,120})(?:\\|([^\\]\\n]{0,120}))?\\]\\]',
    '\\*\\*([^*\\n]+)\\*\\*',
    '\\*([^*\\n]+)\\*',
  ].join('|'),
  'g',
)

/** Parse one line's inline constructs. Unmatched text passes through verbatim. */
export function parseInline(source: string): Inline[] {
  const spans: Inline[] = []
  let cursor = 0

  const pattern = new RegExp(INLINE_PATTERN.source, 'g')
  let match: RegExpExecArray | null
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > cursor) {
      spans.push({ kind: 'text', text: source.slice(cursor, match.index) })
    }
    const [, code, wikiTarget, wikiLabel, strong, em] = match
    if (code !== undefined) {
      spans.push({ kind: 'code', text: code })
    } else if (wikiTarget !== undefined) {
      const slug = slugifyWikiTarget(wikiTarget)
      const label = (wikiLabel ?? wikiTarget).trim() || wikiTarget
      // A target that slugifies to nothing has no destination, so it is text.
      // Rendering it as a link would produce an anchor to the KB index that looks
      // like a reference to a specific article.
      spans.push(slug ? { kind: 'wiki', target: { slug, label } } : { kind: 'text', text: label })
    } else if (strong !== undefined) {
      spans.push({ kind: 'strong', text: strong })
    } else if (em !== undefined) {
      spans.push({ kind: 'em', text: em })
    }
    cursor = match.index + match[0].length
  }

  if (cursor < source.length) spans.push({ kind: 'text', text: source.slice(cursor) })
  return spans
}

const HEADING = /^(#{1,6})\s+(.*)$/
const QUOTE = /^>\s?(.*)$/
const RULE = /^(-{3,}|\*{3,}|_{3,})$/
const BULLET = /^[-*]\s+(.*)$/
const ORDERED = /^\d+[.)]\s+(.*)$/
const FENCE = /^```\s*([A-Za-z0-9+#-]*)\s*$/

/**
 * Parse a markdown document into blocks.
 *
 * Line-oriented and single-pass. Fenced blocks are consumed by the scanner as
 * soon as an opening fence is seen, so their contents never reach the inline
 * parser — which is what the console needed its NUL sentinel to achieve.
 *
 * An UNCLOSED fence takes the rest of the document as code rather than falling
 * back to prose. That is the reading that loses least: a half-written article's
 * remaining text shows up verbatim in a code block, instead of every `*` and `#`
 * in it being silently reinterpreted as formatting.
 */
export function parseMarkdown(source: string): Block[] {
  const lines = (source || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let paragraph: string[] = []

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push({ kind: 'paragraph', spans: parseInline(paragraph.join('\n')) })
    paragraph = []
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const trimmed = line.trim()

    const fence = FENCE.exec(trimmed)
    if (fence) {
      flushParagraph()
      const body: string[] = []
      index += 1
      while (index < lines.length && !FENCE.test(lines[index].trim())) {
        body.push(lines[index])
        index += 1
      }
      blocks.push({ kind: 'code', text: body.join('\n'), language: fence[1] || null })
      continue
    }

    if (!trimmed) {
      flushParagraph()
      continue
    }

    if (RULE.test(trimmed)) {
      flushParagraph()
      blocks.push({ kind: 'rule' })
      continue
    }

    const heading = HEADING.exec(trimmed)
    if (heading) {
      flushParagraph()
      // Collapsed at 4 like the console did: an article is a page, not a book, and
      // an h5 that renders identically to an h4 is noise in the outline.
      const level = Math.min(heading[1].length, 4) as 1 | 2 | 3 | 4
      blocks.push({ kind: 'heading', level, spans: parseInline(heading[2]) })
      continue
    }

    const quote = QUOTE.exec(trimmed)
    if (quote) {
      flushParagraph()
      blocks.push({ kind: 'quote', spans: parseInline(quote[1]) })
      continue
    }

    const bullet = BULLET.exec(trimmed)
    const ordered = bullet ? null : ORDERED.exec(trimmed)
    if (bullet || ordered) {
      flushParagraph()
      const isOrdered = ordered != null
      const items: Inline[][] = []
      // Consume the whole run, so consecutive items are ONE list. Emitting a list
      // per line is what made the console's bullets each their own `<ul>`.
      while (index < lines.length) {
        const candidate = lines[index].trim()
        const nextBullet = BULLET.exec(candidate)
        const nextOrdered = ORDERED.exec(candidate)
        const item = isOrdered ? nextOrdered : nextBullet
        if (!item) break
        items.push(parseInline(item[1]))
        index += 1
      }
      index -= 1
      blocks.push({ kind: 'list', ordered: isOrdered, items })
      continue
    }

    paragraph.push(line)
  }

  flushParagraph()
  return blocks
}

/** Every wiki slug a body references, in first-appearance order. */
export function wikiSlugs(source: string): string[] {
  const seen = new Set<string>()
  const walk = (spans: Inline[]) => {
    for (const span of spans) if (span.kind === 'wiki') seen.add(span.target.slug)
  }
  for (const block of parseMarkdown(source)) {
    if (block.kind === 'list') block.items.forEach(walk)
    else if (block.kind !== 'code' && block.kind !== 'rule') walk(block.spans)
  }
  return [...seen]
}
