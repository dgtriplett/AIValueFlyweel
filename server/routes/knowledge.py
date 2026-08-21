"""Knowledge base — articles, folders, versions, links, attachments.

    GET    /api/kb/tree                    folder tree + article counts
    POST   /api/kb/folders                 create a folder
    PATCH  /api/kb/folders/{id}            rename or move
    DELETE /api/kb/folders/{id}            delete (articles are kept, unfiled)

    GET    /api/kb/articles                list / search / filter
    POST   /api/kb/articles                create
    GET    /api/kb/articles/{slug}         read one (+ versions, links, files)
    PUT    /api/kb/articles/{slug}         edit (snapshots the previous version)
    DELETE /api/kb/articles/{slug}         archive, or ?hard=true to delete
    GET    /api/kb/articles/{slug}/versions        history
    POST   /api/kb/articles/{slug}/restore/{n}     roll back to a version

    POST   /api/kb/articles/{slug}/links   attach to a portfolio entity
    DELETE /api/kb/links/{id}              detach
    GET    /api/kb/for/{entity_type}/{id}  everything attached to one entity

    POST   /api/kb/articles/{slug}/attachments     upload a document
    GET    /api/kb/attachments/{id}                download it
    DELETE /api/kb/attachments/{id}                remove it

WHY EDITS ARE NOT CONFIRM-GATED
-------------------------------
Every AGENT-initiated write in this app goes through the propose/confirm token,
because the agent's judgement is what needs review. Editing an article you are
looking at is not that: it is a person typing into a document. Gating it would add a
confirmation dialog to every keystroke-to-save cycle and teach people to click
through confirmations, which devalues the gate where it matters.

What protects the content instead is versioning — every edit snapshots the previous
body, so a mistake is recoverable and an unwanted change is visible. The
agent-generated proposal path (routes/proposals.py) DOES go through the gate, since
there the model is the author.
"""
from __future__ import annotations

import logging

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     Response, UploadFile)
from pydantic import BaseModel, Field

from .. import accounts
from .. import knowledge as kb
from .. import portfolio
from ..common import current_user, rows_to_list, write_audit
from ..config import ATLAS_CATALOG, ATLAS_SCHEMA, discovery_configured
from ..db import DatabaseUnavailable, db
from ..limits import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["knowledge"])

# Attachments larger than this go to a Unity Catalog Volume when one is available;
# below it, Lakebase is simpler and avoids a file-API round trip for a small file.
# Both paths are always available, so the feature never depends on the discovery
# layer being configured.
_VOLUME_THRESHOLD = 512 * 1024

ENTITY_TYPES = ("use_case", "data_asset", "data_domain", "lob", "roadmap_item",
                "funding_request")
RELATIONS = ("explains", "standard", "proposal", "evidence", "related")

# The table each entity_type lives in, for existence checks. kb_links is
# deliberately polymorphic (no FK), so the route is what keeps a link honest.
_ENTITY_TABLES = {
    "use_case": "use_cases",
    "data_asset": "data_assets",
    "data_domain": "data_domains",
    "lob": "lobs",
    "roadmap_item": "roadmap_items",
    "funding_request": "funding_requests",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class FolderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    parent_id: int | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None
    # Explicit flag because `parent_id: None` is ambiguous in JSON — it means both
    # "move to root" and "not specified", and those are different operations.
    move_to_root: bool = False


class ArticleIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    body_md: str = Field(default="", max_length=500_000)
    summary: str | None = Field(default=None, max_length=1000)
    folder_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=25)
    status: str = "draft"


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body_md: str | None = Field(default=None, max_length=500_000)
    summary: str | None = Field(default=None, max_length=1000)
    folder_id: int | None = None
    tags: list[str] | None = Field(default=None, max_length=25)
    status: str | None = None
    change_note: str | None = Field(default=None, max_length=500)


class LinkIn(BaseModel):
    entity_type: str
    entity_id: int
    relation: str = "related"


# ---------------------------------------------------------------------------
# Account scoping
# ---------------------------------------------------------------------------
# kb_articles and kb_folders both carry account_id (migration 009), but every read
# here spanned all rows and every write omitted it. So one utility's knowledge base
# — their standards, their generated proposals, their uploaded documents — was
# readable and editable by every other tenant on the instance, and anything they
# created became globally visible because account_id defaulted to NULL.
#
# NULL means "shared reference library, visible to all" and is what the shipped
# seeded content uses, so the read predicate keeps NULL rows while the write path
# stamps the current account.
async def _article_by_slug(slug: str, *, columns: str = "id", for_write: bool = False):
    """One article the current account may see, or None.

    Every by-slug handler goes through this rather than querying `WHERE slug = $1`
    directly. Slugs are unique across the whole table, so an unscoped lookup let a
    caller read, edit, archive or hard-delete another tenant's article by guessing
    or listing its slug — and article slugs are derived from titles, so they are
    guessable.

    `for_write=True` uses the strict ownership predicate, excluding the shared
    (`account_id IS NULL`) reference library. Callers that go on to MUTATE the article
    — or anything hanging off it, like links and attachments — must pass it, or a
    tenant can alter the shipped library for every other customer. Read-only callers
    leave it False so shared content stays visible.
    """
    scope, params = (await accounts.write_clause_at(2) if for_write
                     else await accounts.scope_clause_at(2))
    return await db.fetchrow(
        f"SELECT {columns} FROM kb_articles WHERE slug = $1 AND {scope}",
        slug, *params)


async def _folder_visible(folder_id: int) -> bool:
    """Whether the current account may file something in this folder."""
    scope, params = await accounts.scope_clause_at(2)
    row = await db.fetchrow(
        f"SELECT id FROM kb_folders WHERE id = $1 AND {scope}", folder_id, *params)
    return row is not None


# Which of the linkable entity tables carry an account_id of their own.
#
# use_cases, data_assets, data_domains and lobs deliberately do NOT: migration 009
# calls them "shared reference data — the library the product ships", curated once
# and improved for everyone. Copying them per account would fork the catalog.
#
# For use_cases the account-specific part is MEMBERSHIP, held in
# account_portfolio_use_cases (migration 014), so "may this account link to it" is a
# portfolio question, not a column comparison — hence the special case below.
_ACCOUNT_OWNED_ENTITY_TABLES = {"roadmap_items", "funding_requests"}


async def _entity_exists(entity_type: str, entity_id: int) -> bool:
    """Whether the current account may link to this entity.

    WHY THIS IS NOT JUST `SELECT id FROM <table> WHERE id = $1`
    ----------------------------------------------------------
    That version was an existence oracle. `roadmap_items` and `funding_requests` are
    account-owned, so an unscoped probe let a caller walk ids and learn which of
    another tenant's roadmap items and funding requests exist purely from the 404 vs
    200 difference — without ever reading a field.

    The three cases are genuinely different, so they are handled differently rather
    than forced into one predicate:

      * account-owned tables  -> ownership predicate on account_id;
      * use_cases             -> portfolio membership, because the catalog row is
                                 shared but a given account's selection is not;
      * the rest of the shared catalog (data_assets, data_domains, lobs) -> a plain
        existence check is correct. These are the shipped library, identical for
        every tenant, so confirming a row exists discloses nothing account-specific.
    """
    table = _ENTITY_TABLES.get(entity_type)
    if not table:
        return False

    if entity_type == "use_case":
        # Reuses the existing membership helper rather than reimplementing the
        # portfolio predicate, so this cannot drift from how the portfolio is read
        # everywhere else.
        condition, params = await portfolio.portfolio_condition("uc", param_index=2)
        row = await db.fetchrow(
            f"SELECT uc.id FROM use_cases uc WHERE uc.id = $1 AND {condition}",
            entity_id, *params)
        return row is not None

    if table in _ACCOUNT_OWNED_ENTITY_TABLES:
        scope, params = await accounts.owned_clause(param_index=2)
        row = await db.fetchrow(
            f"SELECT id FROM {table} WHERE id = $1 AND {scope}", entity_id, *params)
        return row is not None

    row = await db.fetchrow(f"SELECT id FROM {table} WHERE id = $1", entity_id)
    return row is not None


def _check_status(status: str | None) -> None:
    if status is not None and status not in ("draft", "published", "archived"):
        raise HTTPException(422, "status must be draft, published or archived")


def _clean_tags(tags: list[str] | None) -> list[str]:
    """Lowercase, de-duplicate, drop blanks, preserve order.

    Normalized so "Protection", "protection" and " protection " are one tag rather
    than three that all filter separately.
    """
    seen, out = set(), []
    for tag in tags or []:
        cleaned = (tag or "").strip().lower()[:60]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------
@router.get("/tree")
async def folder_tree():
    """The folder tree with per-folder article counts, plus the unfiled count."""
    # Both the folder list AND the per-folder counts are scoped: an unscoped count
    # leaks the SIZE of another tenant's knowledge base even when the folder itself
    # is shared reference content.
    folder_scope, folder_params = await accounts.scope_clause("f")
    article_scope, _ = await accounts.scope_clause("a")
    folders = rows_to_list(await db.fetch(f"""
        SELECT f.id, f.name, f.parent_id, f.path, f.sort_order,
               (SELECT count(*) FROM kb_articles a
                 WHERE a.folder_id = f.id AND a.status <> 'archived'
                   AND {article_scope})
               AS article_count
        FROM kb_folders f WHERE {folder_scope}
        ORDER BY f.sort_order, lower(f.name)
    """, *folder_params))
    scope, params = await accounts.scope_clause()
    unfiled = await db.fetchrow(
        f"SELECT count(*) AS n FROM kb_articles "
        f"WHERE folder_id IS NULL AND status <> 'archived' AND {scope}", *params)
    totals = await db.fetchrow(
        f"SELECT count(*) AS articles, "
        f"count(*) FILTER (WHERE status = 'published') AS published, "
        f"count(*) FILTER (WHERE generated_by IS NOT NULL) AS generated "
        f"FROM kb_articles WHERE status <> 'archived' AND {scope}", *params)
    return {
        "folders": folders,
        "unfiled_count": (unfiled or {}).get("n", 0) if unfiled else 0,
        "totals": dict(totals) if totals else {},
    }


@router.post("/folders", dependencies=[Depends(limiter("write"))])
async def create_folder(body: FolderIn, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    scope, scope_params = await accounts.scope_clause_at(2)
    parent_path = None
    if body.parent_id is not None:
        parent = await db.fetchrow(
            f"SELECT path FROM kb_folders WHERE id = $1 AND {scope}",
            body.parent_id, *scope_params)
        if parent is None:
            raise HTTPException(404, "Parent folder not found")
        parent_path = parent["path"]

    path = kb.folder_path(parent_path, body.name)
    # Scoped, so two tenants may each have a /standards folder. An unscoped check
    # made the first account to create a path own that name instance-wide, and the
    # 409 disclosed that another tenant had used it.
    existing = await db.fetchrow(
        f"SELECT id FROM kb_folders WHERE path = $1 AND {scope}",
        path, *scope_params)
    if existing is not None:
        raise HTTPException(
            409, f"A folder already exists at {path}. Pick a different name.")

    row = await db.fetchrow("""
        INSERT INTO kb_folders (account_id, name, parent_id, path, created_by)
        VALUES ($1,$2,$3,$4,$5) RETURNING id, name, parent_id, path
    """, account_id, body.name.strip(), body.parent_id, path, actor)
    await write_audit("kb_folder", row["id"], "create", actor, {"path": path})
    return dict(row)


@router.patch("/folders/{folder_id}", dependencies=[Depends(limiter("write"))])
async def update_folder(folder_id: int, body: FolderPatch, request: Request):
    """Rename or move a folder, re-pathing its whole subtree."""
    actor = current_user(request)
    scope, scope_params = await accounts.scope_clause_at(2)
    folder = await db.fetchrow(
        f"SELECT id, name, parent_id, path FROM kb_folders "
        f"WHERE id = $1 AND {scope}", folder_id, *scope_params)
    if folder is None:
        raise HTTPException(404, "Folder not found")

    new_parent_id = folder["parent_id"]
    new_parent_path = None
    if body.move_to_root:
        new_parent_id = None
    elif body.parent_id is not None:
        if body.parent_id == folder_id:
            raise HTTPException(422, "A folder cannot be its own parent.")
        parent = await db.fetchrow(
            f"SELECT id, path FROM kb_folders WHERE id = $1 AND {scope}",
            body.parent_id, *scope_params)
        if parent is None:
            raise HTTPException(404, "Parent folder not found")
        # Moving a folder into its own descendant detaches the whole branch: the
        # path prefix becomes self-referential and the subtree stops rendering.
        if kb.is_circular_move(folder["path"], parent["path"]):
            raise HTTPException(
                422, "That would move the folder inside itself, which would "
                     "detach everything under it.")
        new_parent_id = parent["id"]
        new_parent_path = parent["path"]
    elif folder["parent_id"] is not None:
        parent = await db.fetchrow(
            f"SELECT path FROM kb_folders WHERE id = $1 AND {scope}",
            folder["parent_id"], *scope_params)
        new_parent_path = parent["path"] if parent else None

    new_name = (body.name or folder["name"]).strip()
    new_path = kb.folder_path(new_parent_path, new_name)
    old_path = folder["path"]

    if new_path != old_path:
        clash_scope, clash_params = await accounts.scope_clause_at(3)
        clash = await db.fetchrow(
            f"SELECT id FROM kb_folders WHERE path = $1 AND id <> $2 "
            f"AND {clash_scope}", new_path, folder_id, *clash_params)
        if clash is not None:
            raise HTTPException(409, f"A folder already exists at {new_path}.")

    # write_clause_at, NOT scope_clause_at: the read predicate includes
    # `account_id IS NULL`, which would let this tenant rename a SHARED folder for
    # every other customer on the instance.
    update_scope, update_params = await accounts.write_clause_at(5)
    updated = await db.execute(f"""
        UPDATE kb_folders SET name = $1, parent_id = $2, path = $3,
               updated_at = now() WHERE id = $4 AND {update_scope}
    """, new_name, new_parent_id, new_path, folder_id, *update_params)
    if updated is not None and updated.endswith(" 0"):
        # The lookup above found it (possibly as a shared row) but the write matched
        # nothing, which means it is not ours to change.
        raise HTTPException(
            404, "Folder not found, or it is shared reference content that cannot "
                 "be modified from here.")

    # Re-path descendants. Their stored paths all begin with the old path, so one
    # UPDATE fixes the subtree; doing it per-row would leave a partially-repathed
    # tree if it failed halfway.
    #
    # Scoped too: a shared (NULL-account) folder can have same-path descendants in
    # other tenants, and re-pathing those would silently rewrite another customer's
    # folder tree as a side effect of renaming one of ours.
    if new_path != old_path:
        descendant_scope, descendant_params = await accounts.write_clause_at(5)
        await db.execute(f"""
            UPDATE kb_folders
               SET path = $1 || substring(path from char_length($2) + 1),
                   updated_at = now()
             WHERE path LIKE $3 AND id <> $4 AND {descendant_scope}
        """, new_path, old_path, kb.descendant_pattern(old_path), folder_id,
            *descendant_params)

    await write_audit("kb_folder", folder_id, "update", actor,
                      {"from": old_path, "to": new_path})
    return {"id": folder_id, "name": new_name, "path": new_path,
            "parent_id": new_parent_id}


@router.delete("/folders/{folder_id}", dependencies=[Depends(limiter("write"))])
async def delete_folder(folder_id: int, request: Request):
    """Delete a folder. Its articles survive as unfiled.

    Deleting a container must not delete the content someone wrote in it — that is
    a destructive surprise, and the schema's ON DELETE SET NULL makes the articles
    unfiled rather than gone. Child folders cascade, so their articles unfile too.
    """
    actor = current_user(request)
    scope, scope_params = await accounts.scope_clause_at(2)
    folder = await db.fetchrow(
        f"SELECT path FROM kb_folders WHERE id = $1 AND {scope}",
        folder_id, *scope_params)
    if folder is None:
        raise HTTPException(404, "Folder not found")

    # BOTH halves are scoped, including the inner folder-path subquery. Left
    # unscoped, the subquery matched same-path folders in OTHER accounts, so the
    # reported count included articles belonging to other tenants — a wrong number
    # in a confirmation dialog, derived from data the caller cannot see.
    article_scope, article_params = await accounts.scope_clause_at(2, "a")
    inner_scope, inner_params = await accounts.scope_clause_at(3, "f2")
    affected = await db.fetchrow(f"""
        SELECT count(*) AS n FROM kb_articles a
        WHERE a.folder_id IN (
            SELECT f2.id FROM kb_folders f2 WHERE f2.path LIKE $1 AND {inner_scope}
        ) AND {article_scope}""",
        kb.descendant_pattern(folder["path"]), *article_params, *inner_params)

    # write_clause_at: a shared folder must not be deletable by one tenant for
    # everyone. The lookup above may have matched it as a readable shared row.
    write_scope, write_params = await accounts.write_clause_at(2)
    deleted = await db.execute(
        f"DELETE FROM kb_folders WHERE id = $1 AND {write_scope}",
        folder_id, *write_params)
    if deleted is not None and deleted.endswith(" 0"):
        raise HTTPException(
            404, "Folder not found, or it is shared reference content that cannot "
                 "be deleted from here.")
    await write_audit("kb_folder", folder_id, "delete", actor,
                      {"path": folder["path"]})
    return {"deleted": True,
            "articles_unfiled": (affected or {}).get("n", 0) if affected else 0}


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------
# NOTE ON ROUTE ORDER: every literal path (/tree, /folders, /articles,
# /attachments/..., /for/...) is declared BEFORE any parameterized one that could
# match it. FastAPI matches in declaration order, so /kb/articles/{slug} declared
# first would shadow /kb/articles/search — a bug already fixed three times
# elsewhere in this app, and the reason RESERVED_SLUGS exists as a second defence.
@router.get("/articles")
async def list_articles(
    q: str | None = Query(default=None, max_length=200,
                          description="full-text search"),
    folder_path: str | None = Query(default=None, max_length=400),
    tag: str | None = Query(default=None, max_length=60),
    status: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _check_status(status)
    if (entity_type is None) != (entity_id is None):
        raise HTTPException(422, "entity_type and entity_id must be given together")
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise HTTPException(422, f"entity_type must be one of {ENTITY_TYPES}")

    account_id = await accounts.current()
    sql = kb.build_search_sql(
        has_query=bool(q), folder=bool(folder_path), tag=bool(tag),
        status=bool(status), entity=entity_type is not None,
        account=account_id is not None)
    params: list = []
    if q:
        params.append(q)
    if folder_path:
        params.append(folder_path)
    if tag:
        params.append(tag.strip().lower())
    if status:
        params.append(status)
    if entity_type is not None:
        params.extend([entity_type, entity_id])
    if account_id is not None:
        params.append(account_id)
    params.extend([limit, offset])

    try:
        rows = rows_to_list(await db.fetch(sql, *params))
    except Exception as exc:  # noqa: BLE001
        # A malformed search expression is user error, not a server fault. Postgres
        # raises on some inputs even via websearch_to_tsquery, and a 500 here reads
        # as "the knowledge base is broken".
        logger.info("kb search failed (%s): %s", type(exc).__name__, exc)
        raise HTTPException(
            422, "That search could not be run. Try plain words, or quote a "
                 "phrase.") from exc
    return {"items": rows, "count": len(rows), "limit": limit, "offset": offset}


@router.post("/articles", dependencies=[Depends(limiter("write"))])
async def create_article(body: ArticleIn, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    _check_status(body.status)
    if body.folder_id is not None and not await _folder_visible(body.folder_id):
        raise HTTPException(404, "Folder not found")

    # Slug uniqueness stays GLOBAL on purpose: kb_articles.slug is UNIQUE in the
    # schema (migration 007) and relaxing that needs a migration another PR owns. So
    # the candidate list must span all rows, or the insert below fails on a
    # constraint violation instead of picking the next free suffix. This reads slugs
    # only — no titles, bodies or account ids — so it is not a content disclosure.
    taken = {r["slug"] for r in await db.fetch("SELECT slug FROM kb_articles")}
    slug = kb.slugify(body.title, existing=taken)

    row = await db.fetchrow("""
        INSERT INTO kb_articles
            (account_id, title, slug, folder_id, body_md, summary, tags, status,
             created_by, updated_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)
        RETURNING id, title, slug, status, version, created_at
    """, account_id, body.title.strip(), slug, body.folder_id, body.body_md,
        body.summary, _clean_tags(body.tags), body.status, actor)
    await write_audit("kb_article", row["id"], "create", actor,
                      {"slug": slug, "title": body.title})
    return dict(row)


@router.get("/articles/{slug}")
async def get_article(slug: str):
    """One article with its links, attachments, versions and resolved wiki links."""
    scope, scope_params = await accounts.scope_clause_at(2, "a")
    row = await db.fetchrow(f"""
        SELECT a.*, f.name AS folder_name, f.path AS folder_path
        FROM kb_articles a LEFT JOIN kb_folders f ON f.id = a.folder_id
        WHERE a.slug = $1 AND {scope}
    """, slug, *scope_params)
    if row is None:
        raise HTTPException(404, "Article not found")
    article = dict(row)
    article.pop("search_tsv", None)   # a tsvector is not useful to a client
    article_id = article["id"]

    links = rows_to_list(await db.fetch(
        "SELECT id, entity_type, entity_id, relation, created_by, created_at "
        "FROM kb_links WHERE article_id = $1 ORDER BY entity_type, entity_id",
        article_id))
    # Resolve each link's display name so the UI shows "Outage Prediction" rather
    # than "use_case 42". Grouped by type to keep this to one query per type.
    by_type: dict[str, list[int]] = {}
    for link in links:
        by_type.setdefault(link["entity_type"], []).append(link["entity_id"])
    names: dict[tuple[str, int], str] = {}
    for entity_type, ids in by_type.items():
        table = _ENTITY_TABLES.get(entity_type)
        if not table:
            continue
        column = "name" if entity_type in ("data_domain", "lob") else "title"
        if entity_type == "data_asset":
            column = "module"
        # SCOPED, and the label path needs the SAME rule as validation.
        #
        # Labels are rendered straight into the UI, so an unscoped resolve is a direct
        # disclosure rather than just an existence oracle.
        #
        # use_cases needs membership scoping even though the table is the shared
        # catalog, which is a correction to an earlier assumption here. "Shared
        # catalog" is true of the SHIPPED rows, but `POST /api/use_cases` inserts
        # CUSTOM, customer-authored use cases into that same table and only then adds
        # them to the creator's account_portfolio_use_cases. So a use-case title can be
        # one tenant's private text living in a shared table, and a link created before
        # validation was scoped still points at it. Membership is the only predicate
        # that separates them.
        if entity_type == "use_case":
            condition, condition_params = await portfolio.portfolio_condition(
                "uc", param_index=2)
            label_sql = (f"SELECT uc.id, uc.{column} AS label FROM use_cases uc "
                         f"WHERE uc.id = ANY($1::int[]) AND {condition}")
            label_args = [ids, *condition_params]
        elif table in _ACCOUNT_OWNED_ENTITY_TABLES:
            owned, owned_params = await accounts.owned_clause(param_index=2)
            label_sql = (f"SELECT id, {column} AS label FROM {table} "
                         f"WHERE id = ANY($1::int[]) AND {owned}")
            label_args = [ids, *owned_params]
        else:
            # data_assets, data_domains, lobs: shipped reference data with no
            # per-tenant authoring path, so their labels are identical for every
            # account and disclose nothing account-specific.
            label_sql = (f"SELECT id, {column} AS label FROM {table} "
                         f"WHERE id = ANY($1::int[])")
            label_args = [ids]
        try:
            found = await db.fetch(label_sql, *label_args)
            for item in found:
                names[(entity_type, item["id"])] = item["label"]
        except DatabaseUnavailable:
            # A configured outage must not be swallowed by the nicety-handler below,
            # or the article renders with silently missing labels during an incident.
            raise
        except Exception:  # noqa: BLE001 - a resolvable name is a nicety
            pass
    for link in links:
        link["label"] = names.get((link["entity_type"], link["entity_id"]))

    attachments = rows_to_list(await db.fetch(
        "SELECT id, filename, mime_type, size_bytes, storage, checksum, "
        "uploaded_by, uploaded_at FROM kb_attachments "
        "WHERE article_id = $1 ORDER BY uploaded_at DESC", article_id))
    versions = rows_to_list(await db.fetch(
        "SELECT version, title, change_note, edited_by, edited_at "
        "FROM kb_article_versions WHERE article_id = $1 "
        "ORDER BY version DESC LIMIT 50", article_id))

    # Resolve [[wiki links]] so the UI can render a working link, and show which
    # references point at nothing yet.
    referenced = kb.extract_wiki_links(article.get("body_md") or "")
    resolved: list[dict] = []
    if referenced:
        # Scoped: resolving against all rows returned another tenant's article TITLE
        # for any slug guessed in a body, turning [[...]] into a probe for what other
        # customers have written.
        link_scope, link_params = await accounts.scope_clause_at(2)
        found = await db.fetch(
            f"SELECT slug, title FROM kb_articles "
            f"WHERE slug = ANY($1::text[]) AND {link_scope}",
            referenced, *link_params)
        existing = {r["slug"]: r["title"] for r in found}
        resolved = [{"slug": s, "title": existing.get(s), "exists": s in existing}
                    for s in dict.fromkeys(referenced)]

    return {**article, "links": links, "attachments": attachments,
            "versions": versions, "references": resolved}


@router.put("/articles/{slug}", dependencies=[Depends(limiter("write"))])
async def update_article(slug: str, body: ArticleUpdate, request: Request):
    """Edit an article, snapshotting the previous version first."""
    actor = current_user(request)
    _check_status(body.status)
    # for_write, and it must be checked BEFORE the version snapshot below: a
    # read-scoped lookup would match a shared article, write a kb_article_versions
    # row for it, and only then have the UPDATE match nothing — leaving a phantom
    # version on content the caller never changed.
    row = await _article_by_slug(
        slug, columns="id, title, body_md, summary, version", for_write=True)
    if row is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be modified from here.")

    if body.folder_id is not None and not await _folder_visible(body.folder_id):
        raise HTTPException(404, "Folder not found")

    # Only a CONTENT change makes a new version. Re-filing an article or changing
    # its tags would otherwise fill the history with entries whose diff is empty,
    # and a history nobody can skim is a history nobody reads.
    content_changed = (
        (body.title is not None and body.title.strip() != row["title"])
        or (body.body_md is not None and body.body_md != row["body_md"])
        or (body.summary is not None and body.summary != row["summary"]))

    if content_changed:
        await db.execute("""
            INSERT INTO kb_article_versions
                (article_id, version, title, body_md, summary, change_note, edited_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (article_id, version) DO NOTHING
        """, row["id"], row["version"], row["title"], row["body_md"],
            row["summary"], body.change_note, actor)

    # write_clause_at (strict equality, never NULL): the lookup above is
    # NULL-inclusive so a shared article is READABLE, but editing one would rewrite
    # the shipped library for every tenant on the instance.
    write_scope, write_params = await accounts.write_clause_at(10)
    updated = await db.fetchrow(f"""
        UPDATE kb_articles SET
            title    = COALESCE($2, title),
            body_md  = COALESCE($3, body_md),
            summary  = COALESCE($4, summary),
            folder_id = COALESCE($5, folder_id),
            tags     = COALESCE($6, tags),
            status   = COALESCE($7, status),
            version  = version + CASE WHEN $8 THEN 1 ELSE 0 END,
            updated_by = $9,
            updated_at = now()
        WHERE id = $1 AND {write_scope}
        RETURNING id, title, slug, status, version, updated_at
    """, row["id"],
        body.title.strip() if body.title else None,
        body.body_md, body.summary, body.folder_id,
        _clean_tags(body.tags) if body.tags is not None else None,
        body.status, content_changed, actor, *write_params)
    if updated is None:
        raise HTTPException(404, "Article not found")

    await write_audit("kb_article", row["id"], "update", actor,
                      {"slug": slug, "new_version": updated["version"],
                       "content_changed": content_changed})
    return {**dict(updated), "content_changed": content_changed}


@router.delete("/articles/{slug}", dependencies=[Depends(limiter("write"))])
async def delete_article(slug: str, request: Request, hard: bool = False):
    """Archive by default; `?hard=true` deletes permanently.

    Archiving is the default because an article is somebody's written work and the
    usual intent is "get this out of my way", not "destroy it". Archived articles
    drop out of search but keep their links and history.
    """
    actor = current_user(request)
    row = await _article_by_slug(slug, columns="id, title", for_write=True)
    if row is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be deleted from here.")
    # write_clause_at on BOTH paths. The lookup is NULL-inclusive so a shared article
    # is readable; deleting or archiving one here would remove it for every tenant,
    # and a hard delete is irreversible.
    shared_refusal = ("Article not found, or it is shared reference content that "
                      "cannot be modified from here.")
    if hard:
        scope, params = await accounts.write_clause_at(2)
        result = await db.execute(
            f"DELETE FROM kb_articles WHERE id = $1 AND {scope}",
            row["id"], *params)
        if result is not None and result.endswith(" 0"):
            raise HTTPException(404, shared_refusal)
        await write_audit("kb_article", row["id"], "delete", actor, {"slug": slug})
        return {"deleted": True, "slug": slug}
    scope, params = await accounts.write_clause_at(3)
    result = await db.execute(
        f"UPDATE kb_articles SET status = 'archived', updated_by = $2, "
        f"updated_at = now() WHERE id = $1 AND {scope}", row["id"], actor, *params)
    if result is not None and result.endswith(" 0"):
        raise HTTPException(404, shared_refusal)
    await write_audit("kb_article", row["id"], "archive", actor, {"slug": slug})
    return {"archived": True, "slug": slug}


@router.get("/articles/{slug}/versions")
async def article_versions(slug: str):
    row = await _article_by_slug(slug, columns="id, version")
    if row is None:
        raise HTTPException(404, "Article not found")
    return {
        "current_version": row["version"],
        "versions": rows_to_list(await db.fetch(
            "SELECT version, title, body_md, summary, change_note, edited_by, "
            "edited_at FROM kb_article_versions WHERE article_id = $1 "
            "ORDER BY version DESC", row["id"])),
    }


@router.post("/articles/{slug}/restore/{version}",
             dependencies=[Depends(limiter("write"))])
async def restore_version(slug: str, version: int, request: Request):
    """Roll back to an earlier version.

    The rollback is itself a new version — the current text is snapshotted before
    being replaced, so restoring is undoable and history stays append-only. A
    rollback that discarded the state it replaced would be the one edit you cannot
    recover from.
    """
    actor = current_user(request)
    # for_write, checked before the snapshot INSERT below for the same reason as
    # update_article: otherwise a shared article gains a phantom version row.
    row = await _article_by_slug(
        slug, columns="id, title, body_md, summary, version", for_write=True)
    if row is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be modified from here.")
    target = await db.fetchrow(
        "SELECT title, body_md, summary FROM kb_article_versions "
        "WHERE article_id = $1 AND version = $2", row["id"], version)
    if target is None:
        raise HTTPException(404, f"Version {version} not found for this article")

    await db.execute("""
        INSERT INTO kb_article_versions
            (article_id, version, title, body_md, summary, change_note, edited_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (article_id, version) DO NOTHING
    """, row["id"], row["version"], row["title"], row["body_md"], row["summary"],
        f"Replaced by a restore of version {version}", actor)

    # write_clause_at: restoring rewrites the article's live text, so a shared row
    # must not be reachable here even though it is readable.
    scope, params = await accounts.write_clause_at(6)
    updated = await db.fetchrow(f"""
        UPDATE kb_articles SET title = $2, body_md = $3, summary = $4,
               version = version + 1, updated_by = $5, updated_at = now()
        WHERE id = $1 AND {scope} RETURNING version
    """, row["id"], target["title"], target["body_md"], target["summary"], actor,
        *params)
    if updated is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be modified from here.")

    await write_audit("kb_article", row["id"], "restore", actor,
                      {"slug": slug, "restored_from": version,
                       "new_version": updated["version"]})
    return {"slug": slug, "restored_from": version,
            "version": updated["version"]}


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
@router.post("/articles/{slug}/links", dependencies=[Depends(limiter("write"))])
async def add_link(slug: str, body: LinkIn, request: Request):
    actor = current_user(request)
    if body.entity_type not in ENTITY_TYPES:
        raise HTTPException(422, f"entity_type must be one of {ENTITY_TYPES}")
    if body.relation not in RELATIONS:
        raise HTTPException(422, f"relation must be one of {RELATIONS}")

    # for_write: adding a link mutates the article's relationships, so a shared
    # reference article must not be attachable-to by one tenant for everyone.
    article = await _article_by_slug(slug, for_write=True)
    if article is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be modified from here.")

    # kb_links has no FK (it is polymorphic by design), so the route verifies the
    # target exists. Without this a typo silently creates a link to nothing, and
    # the article looks attached to a use case nobody can find.
    #
    # SCOPED, because this read is an existence oracle: unscoped, a caller could walk
    # entity ids and learn which roadmap items and funding requests exist on other
    # accounts from whether they got a 404 or a 200.
    target = await _entity_exists(body.entity_type, body.entity_id)
    if not target:
        raise HTTPException(
            404, f"No {body.entity_type.replace('_', ' ')} with id "
                 f"{body.entity_id}")

    row = await db.fetchrow("""
        INSERT INTO kb_links (article_id, entity_type, entity_id, relation, created_by)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (article_id, entity_type, entity_id, relation) DO NOTHING
        RETURNING id
    """, article["id"], body.entity_type, body.entity_id, body.relation, actor)
    if row is None:
        # Already linked. Idempotent rather than an error: re-attaching is a no-op,
        # which is what a user double-clicking the button means.
        return {"linked": True, "already_existed": True}
    await write_audit("kb_link", row["id"], "create", actor,
                      {"slug": slug, "entity_type": body.entity_type,
                       "entity_id": body.entity_id, "relation": body.relation})
    return {"linked": True, "id": row["id"], "already_existed": False}


@router.delete("/links/{link_id}", dependencies=[Depends(limiter("write"))])
async def remove_link(link_id: int, request: Request):
    """Detach an article from an entity.

    kb_links has no account_id of its own, so it is scoped through the article that
    owns it. Deleting by link id alone let any caller detach another tenant's
    articles from their portfolio entities.
    """
    actor = current_user(request)
    # write_clause_at on the owning-article predicate: detaching a link MUTATES the
    # article's relationships, so a shared article's links must not be strippable by
    # one tenant for everyone.
    scope, scope_params = await accounts.write_clause_at(2, "a")
    row = await db.fetchrow(
        f"""DELETE FROM kb_links WHERE id = $1 AND EXISTS (
                SELECT 1 FROM kb_articles a
                 WHERE a.id = kb_links.article_id AND {scope})
            RETURNING article_id, entity_type, entity_id""",
        link_id, *scope_params)
    if row is None:
        raise HTTPException(404, "Link not found")
    await write_audit("kb_link", link_id, "delete", actor, dict(row))
    return {"deleted": True}


@router.get("/for/{entity_type}/{entity_id}")
async def articles_for_entity(entity_type: str, entity_id: int):
    """Everything in the knowledge base attached to one portfolio entity.

    This is what makes the KB useful from the portfolio side: opening a use case
    should show its standard and its proposal, not require a search.
    """
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(422, f"entity_type must be one of {ENTITY_TYPES}")
    # Scoped through the joined article: opening a use case must show OUR standards
    # and proposals, not every tenant's.
    scope, scope_params = await accounts.scope_clause_at(3, "a")
    rows = rows_to_list(await db.fetch(f"""
        SELECT a.id, a.title, a.slug, a.summary, a.status, a.tags,
               a.generated_by, a.updated_at, l.relation, l.id AS link_id,
               f.path AS folder_path
        FROM kb_links l
        JOIN kb_articles a ON a.id = l.article_id
        LEFT JOIN kb_folders f ON f.id = a.folder_id
        WHERE l.entity_type = $1 AND l.entity_id = $2 AND a.status <> 'archived'
          AND {scope}
        ORDER BY CASE l.relation
                     WHEN 'standard' THEN 1 WHEN 'proposal' THEN 2
                     WHEN 'explains' THEN 3 WHEN 'evidence' THEN 4 ELSE 5 END,
                 a.updated_at DESC
    """, entity_type, entity_id, *scope_params))
    return {"entity_type": entity_type, "entity_id": entity_id,
            "articles": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
@router.post("/articles/{slug}/attachments",
             dependencies=[Depends(limiter("write"))])
async def upload_attachment(slug: str, request: Request,
                            file: UploadFile = File(...)):
    """Attach a document. Validated against its actual bytes, not its filename."""
    actor = current_user(request)
    # for_write: uploading mutates the article, and a tenant must not be able to
    # attach files to the shared reference library that every account then sees.
    article = await _article_by_slug(slug, for_write=True)
    if article is None:
        raise HTTPException(
            404, "Article not found, or it is shared reference content that cannot "
                 "be modified from here.")

    content = await file.read()
    try:
        mime, checksum = kb.validate_attachment(
            file.filename or "attachment", file.content_type or "", content)
    except kb.AttachmentRejected as exc:
        raise HTTPException(422, str(exc)) from exc

    # Same bytes already attached here: return the existing row instead of storing
    # a second copy. Re-uploading after a failed page load is common.
    existing = await db.fetchrow(
        "SELECT id, filename FROM kb_attachments "
        "WHERE article_id = $1 AND checksum = $2", article["id"], checksum)
    if existing is not None:
        return {"id": existing["id"], "filename": existing["filename"],
                "already_existed": True,
                "note": "This exact file is already attached."}

    filename = kb.safe_volume_filename(file.filename or "attachment")
    storage, volume_path = "lakebase", None

    if len(content) >= _VOLUME_THRESHOLD and discovery_configured():
        try:
            volume_path = await _write_to_volume(filename, checksum, content)
            storage = "volume"
        except Exception as exc:  # noqa: BLE001
            # Falling back keeps the upload working when the Volume is not set up
            # or not writable. Logged at warning because a silent fallback would
            # hide a misconfiguration until storage costs showed up in Lakebase.
            logger.warning("volume write failed (%s: %s) — storing in Lakebase",
                           type(exc).__name__, exc)

    row = await db.fetchrow("""
        INSERT INTO kb_attachments
            (article_id, filename, mime_type, size_bytes, storage, volume_path,
             content, checksum, uploaded_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING id, filename, mime_type, size_bytes, storage, uploaded_at
    """, article["id"], file.filename or filename, mime, len(content), storage,
        volume_path, None if storage == "volume" else content, checksum, actor)

    await write_audit("kb_attachment", row["id"], "upload", actor,
                      {"slug": slug, "bytes": len(content), "storage": storage})
    return {**dict(row), "already_existed": False}


async def _write_to_volume(filename: str, checksum: str, content: bytes) -> str:
    """Write bytes to the discovery Volume and return the path.

    The checksum prefixes the stored name so two different files uploaded with the
    same filename cannot overwrite each other — a real hazard when every utility
    names its file "standard.pdf".
    """
    from ..config import get_workspace_client

    volume = f"/Volumes/{ATLAS_CATALOG}/{ATLAS_SCHEMA}/kb_attachments"
    path = f"{volume}/{checksum[:12]}_{filename}"
    client = get_workspace_client()
    # The volume must exist. Created here rather than in the migration because a
    # migration runs against Postgres, not Unity Catalog.
    try:
        client.volumes.create(catalog_name=ATLAS_CATALOG, schema_name=ATLAS_SCHEMA,
                              name="kb_attachments", volume_type="MANAGED")
    except Exception:  # noqa: BLE001 - already exists is the common case
        pass
    import io
    client.files.upload(path, io.BytesIO(content), overwrite=True)
    return path


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: int):
    """Serve an attachment back.

    ALWAYS as an attachment download, never inline. These bytes are user-supplied;
    rendering one in the page would make a crafted file a script-execution vector
    even with the type sniffing on upload.

    Scoped through the owning article. kb_attachments has no account_id, and the ids
    are sequential integers, so fetching by id alone let anyone walk /kb/attachments/1
    upward and download every document every customer had uploaded.
    """
    scope, scope_params = await accounts.scope_clause_at(2, "a")
    row = await db.fetchrow(
        f"""SELECT t.filename, t.mime_type, t.storage, t.volume_path, t.content
            FROM kb_attachments t
            JOIN kb_articles a ON a.id = t.article_id
            WHERE t.id = $1 AND {scope}""", attachment_id, *scope_params)
    if row is None:
        raise HTTPException(404, "Attachment not found")

    if row["storage"] == "volume":
        try:
            from ..config import get_workspace_client
            response = get_workspace_client().files.download(row["volume_path"])
            content = response.contents.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("volume download failed for %s (%s)",
                           row["volume_path"], type(exc).__name__)
            raise HTTPException(
                502, "The file is stored in Unity Catalog but could not be read. "
                     "Check the app's grants on the volume.") from exc
    else:
        content = bytes(row["content"] or b"")

    # A quoted filename with the quotes stripped out of the value: a filename
    # containing a quote would otherwise break out of the header.
    safe_name = (row["filename"] or "attachment").replace('"', "").replace("\n", "")
    return Response(
        content=content,
        media_type=row["mime_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=300",
        })


@router.delete("/attachments/{attachment_id}",
               dependencies=[Depends(limiter("write"))])
async def delete_attachment(attachment_id: int, request: Request):
    """Remove an attachment from an article the current account owns."""
    actor = current_user(request)
    # write_clause_at: removing an attachment mutates the owning article, so a shared
    # article's attachments must not be deletable by a single tenant.
    scope, scope_params = await accounts.write_clause_at(2, "a")
    row = await db.fetchrow(
        f"""DELETE FROM kb_attachments WHERE id = $1 AND EXISTS (
                SELECT 1 FROM kb_articles a
                 WHERE a.id = kb_attachments.article_id AND {scope})
            RETURNING article_id, filename, storage, volume_path""",
        attachment_id, *scope_params)
    if row is None:
        raise HTTPException(404, "Attachment not found")
    # The Volume file is deliberately left in place. Deleting it would be an
    # unrecoverable action taken on a governed store as a side effect of removing a
    # reference; an orphaned file is cheap and auditable.
    await write_audit("kb_attachment", attachment_id, "delete", actor,
                      {"filename": row["filename"], "storage": row["storage"]})
    return {"deleted": True, "volume_file_retained": row["storage"] == "volume"}
