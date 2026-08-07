-- Grid Atlas schema — CHUNK G: the knowledge base.
-- Idempotent: safe to re-run.
--
-- WHAT THIS IS FOR
-- ----------------
-- The portfolio records WHAT a utility intends to build and what it is worth. It
-- has had nowhere to record WHY: the standard a use case has to meet, the reason a
-- source was rejected, the proposal that got a project funded, the interconnection
-- study a decision rested on. Those live in email threads and personal drives, and
-- when the person who wrote them changes roles the reasoning is gone while the
-- portfolio row remains — which is how a utility ends up re-litigating a decision
-- it already made.
--
-- So: markdown articles in a folder tree, attachable to the use cases, domains and
-- data assets they explain, with every edit versioned and binary documents
-- (PDF/DOCX/XLSX/PPTX) attached alongside.
--
-- WHY MARKDOWN AND NOT A RICH TEXT BLOB
-- -------------------------------------
-- It diffs, it greps, it survives an export, and the proposal agent can generate it
-- directly. A rich-text format would make the agent's output the odd one out and
-- make versioning show noise instead of changes.

-- ---------------------------------------------------------------------------
-- Folders
-- ---------------------------------------------------------------------------
-- A materialized path rather than a recursive CTE over parent_id: the tree is small
-- (tens of folders), it is almost always read whole to render a sidebar, and a LIKE
-- on the path answers "everything under here" without recursion. parent_id is kept
-- for integrity and for cheap reparenting of a single node.
CREATE TABLE IF NOT EXISTS kb_folders (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   INT REFERENCES kb_folders(id) ON DELETE CASCADE,
    -- Slash-delimited, leading and trailing slash, e.g. '/standards/protection/'.
    -- Maintained by the route on create/rename/move.
    path        TEXT NOT NULL,
    sort_order  INT NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Two folders cannot share a name under one parent, or the path stops being an
-- identifier. A partial index handles the root, where parent_id IS NULL and a plain
-- UNIQUE would let unlimited duplicates through (NULL != NULL).
CREATE UNIQUE INDEX IF NOT EXISTS kb_folders_unique_child
    ON kb_folders (parent_id, lower(name)) WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS kb_folders_unique_root
    ON kb_folders (lower(name)) WHERE parent_id IS NULL;
CREATE INDEX IF NOT EXISTS kb_folders_path ON kb_folders (path);

-- ---------------------------------------------------------------------------
-- Articles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_articles (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    -- URL-safe identifier, unique across the instance so an article can be linked
    -- by name rather than by an integer id that means nothing to a reader.
    slug         TEXT NOT NULL UNIQUE,
    folder_id    INT REFERENCES kb_folders(id) ON DELETE SET NULL,
    body_md      TEXT NOT NULL DEFAULT '',
    summary      TEXT,
    -- Free-form labels. A text[] rather than a join table: tags are only ever
    -- filtered on and displayed, never joined to anything with attributes.
    tags         TEXT[] NOT NULL DEFAULT '{}',
    -- 'draft' | 'published' | 'archived'. Draft keeps a half-written standard out
    -- of search results without needing a separate table.
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'published', 'archived')),
    -- Set when an agent produced the body, so a reader can tell generated prose
    -- from a human-authored standard. This distinction matters more than it looks:
    -- an unreviewed generated proposal must not be mistaken for policy.
    generated_by TEXT,
    -- Bumped on every content change; the matching row in kb_article_versions
    -- holds the previous text.
    version      INT NOT NULL DEFAULT 1,
    created_by   TEXT,
    updated_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_articles_folder ON kb_articles (folder_id);
CREATE INDEX IF NOT EXISTS kb_articles_status ON kb_articles (status);
CREATE INDEX IF NOT EXISTS kb_articles_tags ON kb_articles USING gin (tags);

-- Full-text search over title + summary + body, weighted so a title match ranks
-- above a passing mention in the body.
--
-- A GENERATED column, not a trigger: it cannot drift from the row it describes, and
-- there is no trigger function to keep in sync with the column list. 'english' is
-- hardcoded because to_tsvector must be IMMUTABLE for an index, which rules out
-- reading the config at runtime.
ALTER TABLE kb_articles
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(body_md, '')), 'C')
    ) STORED;
CREATE INDEX IF NOT EXISTS kb_articles_search ON kb_articles USING gin (search_tsv);

-- Trigram index for substring matching, which full-text search cannot do: a user
-- looking for "SAIDI" should find "SAIDI/SAIFI" and someone typing a partial
-- equipment code should get hits. Skipped without pg_trgm rather than failing the
-- migration — search still works, just without infix matching.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS kb_articles_title_trgm
        ON kb_articles USING gin (title gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR undefined_file THEN
    RAISE NOTICE 'pg_trgm unavailable; substring search will fall back to ILIKE';
END $$;

-- ---------------------------------------------------------------------------
-- Version history
-- ---------------------------------------------------------------------------
-- Every edit writes the PREVIOUS body here before overwriting, so kb_articles
-- always holds current state and history is append-only. Storing full bodies rather
-- than diffs: an article is a few KB, and a full snapshot restores in one statement
-- with no replay logic to get wrong.
CREATE TABLE IF NOT EXISTS kb_article_versions (
    id          SERIAL PRIMARY KEY,
    article_id  INT NOT NULL REFERENCES kb_articles(id) ON DELETE CASCADE,
    version     INT NOT NULL,
    title       TEXT NOT NULL,
    body_md     TEXT NOT NULL,
    summary     TEXT,
    -- What changed and why, when the editor said. Free text; the UI prompts for it
    -- but does not require it, because forcing a message produces "update".
    change_note TEXT,
    edited_by   TEXT,
    edited_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, version)
);
CREATE INDEX IF NOT EXISTS kb_versions_article
    ON kb_article_versions (article_id, version DESC);

-- ---------------------------------------------------------------------------
-- Links to portfolio entities
-- ---------------------------------------------------------------------------
-- A polymorphic (entity_type, entity_id) pair rather than one nullable FK column
-- per target type. Deliberate tradeoff: no referential integrity from this table to
-- the target, in exchange for attaching an article to a new kind of thing without a
-- migration. The route validates entity_type against a closed vocabulary, and a
-- dangling link degrades to "linked to something that no longer exists" rather than
-- corrupting anything.
CREATE TABLE IF NOT EXISTS kb_links (
    id          SERIAL PRIMARY KEY,
    article_id  INT NOT NULL REFERENCES kb_articles(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL
                CHECK (entity_type IN ('use_case', 'data_asset', 'data_domain',
                                       'lob', 'roadmap_item', 'funding_request')),
    entity_id   INT NOT NULL,
    -- Why this article is attached: 'explains' | 'standard' | 'proposal' |
    -- 'evidence' | 'related'. Lets the UI say "the standard for this" instead of
    -- listing every attachment identically.
    relation    TEXT NOT NULL DEFAULT 'related'
                CHECK (relation IN ('explains', 'standard', 'proposal',
                                    'evidence', 'related')),
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One link per (article, entity, relation): re-attaching is a no-op instead of
    -- silently accumulating duplicate rows that all render.
    UNIQUE (article_id, entity_type, entity_id, relation)
);
CREATE INDEX IF NOT EXISTS kb_links_entity ON kb_links (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS kb_links_article ON kb_links (article_id);

-- ---------------------------------------------------------------------------
-- Binary attachments
-- ---------------------------------------------------------------------------
-- Utilities' source material is PDFs and Office files: an interconnection study, a
-- protection standard, a rate case exhibit. Those have to live next to the article
-- that references them or the article is an index to documents nobody can open.
--
-- WHERE THE BYTES GO
-- ------------------
-- A Unity Catalog VOLUME when ATLAS_CATALOG is configured, because these are
-- multi-MB files and a governed Volume is the right home for customer documents —
-- it inherits UC permissions and shows up in lineage. This table then holds only
-- the metadata plus the volume path.
--
-- Lakebase bytea is the fallback for an install with no discovery layer, so the
-- feature does not simply vanish. `storage` records which, because the two are
-- read back differently and a row cannot be interpreted without knowing.
CREATE TABLE IF NOT EXISTS kb_attachments (
    id           SERIAL PRIMARY KEY,
    article_id   INT NOT NULL REFERENCES kb_articles(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    mime_type    TEXT NOT NULL,
    size_bytes   BIGINT NOT NULL,
    -- 'volume' | 'lakebase'
    storage      TEXT NOT NULL CHECK (storage IN ('volume', 'lakebase')),
    -- Set when storage='volume'. The full dbfs:/Volumes/... path.
    volume_path  TEXT,
    -- Set when storage='lakebase'.
    content      BYTEA,
    -- sha256 of the bytes. Lets a re-upload of the same document be recognised
    -- instead of stored twice, and gives an integrity check on read-back.
    checksum     TEXT,
    uploaded_by  TEXT,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Exactly one storage location must be populated. Without this a row could
    -- claim 'volume' with a NULL path and fail only when someone clicks download.
    CONSTRAINT kb_attachments_storage_consistent CHECK (
        (storage = 'volume'   AND volume_path IS NOT NULL AND content IS NULL) OR
        (storage = 'lakebase' AND content IS NOT NULL AND volume_path IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS kb_attachments_article ON kb_attachments (article_id);
CREATE INDEX IF NOT EXISTS kb_attachments_checksum ON kb_attachments (checksum);

-- ---------------------------------------------------------------------------
-- Seed the folder tree
-- ---------------------------------------------------------------------------
-- An empty knowledge base gives no clue what belongs in it. These four folders name
-- the kinds of document a utility actually has, so the first user files something
-- instead of staring at a "New folder" button. Left empty of articles: seeded
-- content would be generic filler that has to be deleted.
INSERT INTO kb_folders (name, path, sort_order, created_by)
VALUES
    ('Standards & Specifications', '/standards-specifications/', 10, 'seed'),
    ('Proposals & Business Cases', '/proposals-business-cases/', 20, 'seed'),
    ('Studies & Assessments',      '/studies-assessments/',      30, 'seed'),
    ('Runbooks & Procedures',      '/runbooks-procedures/',      40, 'seed')
ON CONFLICT DO NOTHING;
