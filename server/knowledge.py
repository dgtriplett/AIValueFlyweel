"""Knowledge-base domain logic: slugs, folder paths, search queries, upload rules.

Kept out of the route module for the usual reason — this is the part worth testing
without a database, and the route should read as HTTP plumbing over it.

The MIME handling here is the security-relevant part. These bytes are uploaded by
one user and served back to another, so the declared type has to be verified
against the actual content rather than trusted: a file claiming to be a PDF that is
really HTML becomes stored XSS the moment a browser renders it.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------
_SLUG_STRIP = re.compile(r"[^\w\s-]")
_SLUG_SPACES = re.compile(r"[-\s]+")

# Reserved because the routes use these as literal path segments. An article
# slugged "search" would be shadowed by /kb/articles/search and unreachable —
# the same route-shadowing class of bug already fixed three times in this app.
RESERVED_SLUGS = frozenset({
    "search", "new", "create", "tree", "folders", "attachments", "versions",
    "export", "import", "propose", "generate", "links", "recent",
})

MAX_SLUG_LENGTH = 80


def slugify(title: str, *, existing: set[str] | None = None) -> str:
    """URL-safe slug for a title, unique against `existing`.

    Unicode is normalized to ASCII rather than percent-encoded: a slug appears in
    URLs, in exports, and in `[[wiki links]]`, and an escaped one is unreadable in
    all three. Names like "Réseau" become "reseau".
    """
    normalized = unicodedata.normalize("NFKD", title or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("", ascii_only).strip().lower()
    slug = _SLUG_SPACES.sub("-", slug).strip("-")[:MAX_SLUG_LENGTH].strip("-")

    # A title of only punctuation or non-Latin script leaves nothing. Falling back
    # to a hash keeps the slug stable for the same title instead of depending on a
    # row id that does not exist yet at insert time.
    if not slug:
        slug = "article-" + hashlib.sha256(
            (title or "").encode("utf-8")).hexdigest()[:8]

    if slug in RESERVED_SLUGS:
        slug = f"{slug}-article"

    if existing is None or slug not in existing:
        return slug

    # Numeric suffix, skipping any already taken.
    base = slug[:MAX_SLUG_LENGTH - 4]
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in existing:
            return candidate
    # Practically unreachable; a hash beats raising on a title collision.
    return f"{base}-{hashlib.sha256(slug.encode()).hexdigest()[:6]}"


# ---------------------------------------------------------------------------
# Folder paths
# ---------------------------------------------------------------------------
def folder_path(parent_path: str | None, name: str) -> str:
    """Materialized path for a folder: '/parent/child/'.

    Always leading AND trailing slash, so `path LIKE parent || '%'` matches the
    whole subtree without also matching a sibling that shares a name prefix —
    '/standards-2/' must not match a query for everything under '/standards/'.
    """
    segment = slugify(name) or "folder"
    if not parent_path or parent_path == "/":
        return f"/{segment}/"
    return f"{parent_path.rstrip('/')}/{segment}/"


def descendant_pattern(path: str) -> str:
    """LIKE pattern matching a folder and everything beneath it.

    Escapes the LIKE metacharacters. A folder named "50%_load" would otherwise make
    `%` a wildcard and match unrelated subtrees.
    """
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def is_circular_move(folder_path_value: str, new_parent_path: str) -> bool:
    """True if moving a folder under `new_parent_path` would orphan the subtree.

    Moving a folder into its own descendant detaches that whole branch from the
    tree: the path prefix becomes self-referential, the sidebar silently stops
    rendering those articles, and they are only findable via search. Cheap to check,
    invisible if missed.
    """
    if not folder_path_value or not new_parent_path:
        return False
    return (new_parent_path == folder_path_value
            or new_parent_path.startswith(folder_path_value))


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
# 25MB. An interconnection study or a rate-case exhibit is routinely 5-15MB, so a
# smaller cap would reject the real documents this feature exists to hold. Larger
# starts to be a data-transfer problem for a request-scoped upload.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Closed vocabulary. Anything not listed is refused rather than stored with a
# guessed type, because the stored MIME is what the download route serves it back
# as — an unrestricted type list is a stored-XSS vector.
ALLOWED_ATTACHMENT_TYPES: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        (".docx",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        (".pptx",),
    "application/msword": (".doc",),
    "application/vnd.ms-excel": (".xls",),
    "application/vnd.ms-powerpoint": (".ppt",),
    "text/csv": (".csv",),
    "text/markdown": (".md",),
    "text/plain": (".txt",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
}


class AttachmentRejected(ValueError):
    """An upload failed validation. The message is shown to the user."""


def sniff_mime(content: bytes) -> str | None:
    """Identify a file from its leading bytes, or None if unrecognised.

    Magic numbers, not the filename or the browser's Content-Type — both are
    attacker-controlled. The modern Office formats are ZIP containers, so they are
    indistinguishable by header alone; that ambiguity is resolved in
    `validate_attachment` by requiring the declared type to be a plausible reading
    of the sniffed one.
    """
    if not content:
        return None
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "application/zip"          # docx / xlsx / pptx
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"  # legacy doc / xls / ppt
    return None


# Sniffed type -> the declared types it may legitimately be.
_SNIFF_COMPATIBLE: dict[str, frozenset[str]] = {
    "application/zip": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }),
    "application/x-ole-storage": frozenset({
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }),
}

# Text formats have no magic number, so sniffing cannot confirm them. They are
# accepted on the declared type alone — safe because the download route serves
# every attachment with Content-Disposition: attachment and a restrictive CSP, so
# even genuine HTML in a .txt is downloaded rather than rendered.
_UNSNIFFABLE = frozenset({"text/csv", "text/markdown", "text/plain"})


def validate_attachment(filename: str, declared_mime: str,
                        content: bytes) -> tuple[str, str]:
    """Check an upload. Returns (normalized_mime, sha256_hex) or raises.

    Rejects on: empty, over the cap, unlisted type, extension not matching the
    type, or content that does not look like what it claims.
    """
    if not content:
        raise AttachmentRejected("The file is empty.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise AttachmentRejected(
            f"{filename} is {len(content) // 1024 // 1024}MB; the limit is "
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024}MB. Link to it in the article "
            "body instead of attaching it.")

    mime = (declared_mime or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_ATTACHMENT_TYPES:
        raise AttachmentRejected(
            f"{mime or 'unknown'} is not an accepted file type. Accepted: "
            + ", ".join(sorted({ext for exts in ALLOWED_ATTACHMENT_TYPES.values()
                                for ext in exts})) + ".")

    lowered = (filename or "").lower()
    if not lowered.endswith(ALLOWED_ATTACHMENT_TYPES[mime]):
        raise AttachmentRejected(
            f"{filename} does not have the extension expected for {mime} "
            f"({', '.join(ALLOWED_ATTACHMENT_TYPES[mime])}). Renaming a file does "
            "not change its format.")

    if mime not in _UNSNIFFABLE:
        sniffed = sniff_mime(content)
        if sniffed is None:
            raise AttachmentRejected(
                f"{filename} does not look like a valid {mime} file — its contents "
                "do not match any recognised format.")
        allowed = _SNIFF_COMPATIBLE.get(sniffed, frozenset({sniffed}))
        if mime not in allowed:
            raise AttachmentRejected(
                f"{filename} claims to be {mime} but its contents look like "
                f"{sniffed}. Check the file is what you expect.")

    return mime, hashlib.sha256(content).hexdigest()


def safe_volume_filename(filename: str) -> str:
    """Strip a filename down to something safe as a single path segment.

    Path traversal defence for the Volume writer: '../../etc/passwd' must not
    escape the attachment directory. Everything but the basename is discarded and
    the remainder is restricted to a conservative character set.
    """
    basename = re.split(r"[\\/]", filename or "")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", basename).lstrip(".")
    return cleaned[:120] or "attachment"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def build_search_sql(*, has_query: bool, folder: bool, tag: bool,
                     status: bool, entity: bool, account: bool = False) -> str:
    """Assemble the article-search statement for the filters in play.

    Built here rather than inline so the shape is testable without a database, and
    so every branch uses $n bind parameters. websearch_to_tsquery is used over
    plain_to_tsquery because it accepts what people actually type — quoted phrases,
    OR, a leading minus to exclude — without erroring on syntax the way
    to_tsquery does.

    `account` adds the tenant predicate. It is a parameter rather than always-on
    because the caller resolves the account id and there is a legitimate unscoped
    case (no accounts configured yet), but the search route always passes it when an
    account resolves — an unscoped search returned every tenant's articles, titles
    and highlighted body excerpts included.
    """
    select = [
        "a.id", "a.title", "a.slug", "a.summary", "a.tags", "a.status",
        "a.folder_id", "a.generated_by", "a.version",
        "a.updated_by", "a.updated_at",
        "f.name AS folder_name", "f.path AS folder_path",
        "(SELECT count(*) FROM kb_attachments t WHERE t.article_id = a.id)"
        " AS attachment_count",
        "(SELECT count(*) FROM kb_links l WHERE l.article_id = a.id)"
        " AS link_count",
    ]
    where: list[str] = []
    params = 0

    if has_query:
        params += 1
        rank_param = params
        # Rank by relevance, and surface a highlighted excerpt so a result list
        # shows WHY each row matched instead of just its title.
        # ts_rank_cd (cover density) rather than ts_rank: it accounts for how close
        # the matched terms are to each other, so an article that uses the phrase
        # together outranks one that mentions each word in different sections.
        select.append(
            "ts_rank_cd(a.search_tsv, "
            f"websearch_to_tsquery('english', ${rank_param})) AS rank")
        select.append(
            f"ts_headline('english', a.body_md, "
            f"websearch_to_tsquery('english', ${rank_param}), "
            "'MaxFragments=1,MaxWords=28,MinWords=8,StartSel=<<,StopSel=>>')"
            " AS excerpt")
        # The tsquery match OR a trigram-style title match, so a partial word still
        # finds an article — full-text search alone misses infix matches.
        where.append(
            f"(a.search_tsv @@ websearch_to_tsquery('english', ${rank_param})"
            f" OR a.title ILIKE '%' || ${rank_param} || '%')")

    if folder:
        params += 1
        # Subtree, not just direct children: asking for /standards/ should return
        # everything filed beneath it.
        where.append(
            f"(f.path = ${params} OR f.path LIKE ${params} || '%')")
    if tag:
        params += 1
        where.append(f"a.tags @> ARRAY[${params}]::text[]")
    if status:
        params += 1
        where.append(f"a.status = ${params}")
    else:
        # Archived articles are excluded unless explicitly asked for; they are kept
        # for the record, not for browsing.
        where.append("a.status <> 'archived'")
    if entity:
        params += 2
        where.append(
            f"EXISTS (SELECT 1 FROM kb_links l WHERE l.article_id = a.id "
            f"AND l.entity_type = ${params - 1} AND l.entity_id = ${params})")
    if account:
        params += 1
        # NULL account_id is the shipped reference library, visible to every tenant.
        where.append(
            f"(a.account_id = ${params} OR a.account_id IS NULL)")

    sql = ("SELECT " + ", ".join(select)
           + " FROM kb_articles a LEFT JOIN kb_folders f ON f.id = a.folder_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Relevance first when searching, recency otherwise.
    sql += " ORDER BY rank DESC, a.updated_at DESC" if has_query \
        else " ORDER BY a.updated_at DESC"
    sql += f" LIMIT ${params + 1} OFFSET ${params + 2}"
    return sql


def extract_wiki_links(body_md: str) -> list[str]:
    """Slugs referenced as [[wiki links]] in a body.

    Lets an author cross-reference by name and have the KB resolve it, which is how
    a set of articles becomes navigable rather than a flat list.

    The closing `]]` is REQUIRED. Matching an unterminated `[[` treated a stray
    bracket — or a code sample containing one — as a link to whatever followed it to
    the end of the paragraph, producing a phantom broken link the author could not
    see the cause of.
    """
    return [slugify(match.strip())
            for match in re.findall(r"\[\[([^\]|\n]{1,120})(?:\|[^\]\n]{0,120})?\]\]",
                                    body_md or "")
            if match.strip()]
