-- Grid Atlas schema — CHUNK C: the discovery layer.
-- Idempotent: safe to re-run.
--
-- WHAT THIS ADDS
-- --------------
-- CHUNKS A/B describe the portfolio a utility WANTS (use cases, semantic data
-- needs, value). This chunk describes the data it ACTUALLY HAS, and connects the
-- two. Three concerns:
--
--   1. INVENTORY  — workspace metadata swept from system.information_schema by
--      schema-extractor/, plus the AI enrichment of it. Lands in
--      discovered_schemas / discovered_tables.
--
--   2. CANONICALIZATION — free-text source-system labels collapse to a canonical
--      vocabulary. This exists because LLM-labelled source systems produce ~1,000
--      distinct strings for ~50 real systems ("PI Historian" / "OSIsoft PI" /
--      "AVEVA PI"), which makes every downstream rollup useless. Lands in
--      data_asset_aliases, resolved against the shipped source_category vocab.
--
--   3. TAXONOMY — effective-dated, AI-assigned classification of data assets
--      across a closed set of dimensions (integration pattern, criticality,
--      vendor type). Lands in asset_taxonomy.
--
-- All three are ADDITIVE and independent: an install that never uploads an
-- inventory behaves exactly as it did before, and the app degrades to the
-- hand-curated catalog. Nothing here is required for readiness or value.

-- ---------------------------------------------------------------------------
-- Ingestion runs — one row per upload/enrichment, for progress + idempotency
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id            SERIAL PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN
                      ('inventory_upload','ai_enrichment','canonicalization',
                       'taxonomy','asset_mapping','lineage_sync')),
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','partial')),
    -- Free-form progress counters, e.g. {"schemas": 812, "tables": 40311}.
    stats_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    error         TEXT,
    actor         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_kind ON ingestion_runs(kind, started_at DESC);

-- ---------------------------------------------------------------------------
-- Discovered schemas (raw inventory + enrichment)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovered_schemas (
    id              SERIAL PRIMARY KEY,
    -- Natural key: the same schema in two workspaces is two rows (that IS the
    -- multi-workspace signal — e.g. a schema present in prod but not dev).
    workspace_id    TEXT NOT NULL,
    workspace_url   TEXT,
    catalog_name    TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    owner           TEXT,
    comment         TEXT,
    table_count     INT NOT NULL DEFAULT 0,
    source_created_at   TIMESTAMPTZ,
    source_altered_at   TIMESTAMPTZ,
    -- AI enrichment (written by the staged ai_query pass).
    business_name   TEXT,
    ai_definition   TEXT,
    ai_confidence   NUMERIC,
    -- Never overwritten by the enrichment job once a human edits it.
    is_user_edited  BOOLEAN NOT NULL DEFAULT false,
    -- false once a later sweep no longer sees it (soft-delete, keeps history).
    is_present      BOOLEAN NOT NULL DEFAULT true,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, catalog_name, schema_name)
);

-- ---------------------------------------------------------------------------
-- Discovered tables (raw inventory + enrichment + source-system attribution)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovered_tables (
    id              SERIAL PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    catalog_name    TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    table_type      TEXT,
    data_format     TEXT,
    owner           TEXT,
    comment         TEXT,
    -- Comma-separated column names + types, captured for enrichment context.
    -- Denormalized deliberately: we never query individual columns, we only feed
    -- them to the model, and a columns table on a 50k-table estate is millions of
    -- rows of storage for no query benefit.
    column_summary  TEXT,
    column_count    INT,
    source_created_at   TIMESTAMPTZ,
    source_altered_at   TIMESTAMPTZ,
    -- AI enrichment.
    business_name   TEXT,
    ai_definition   TEXT,
    -- Raw free-text source system as the model labelled it. Preserved verbatim.
    source_system_raw       TEXT,
    -- Resolved canonical (FK-by-value to data_assets.source_category vocabulary).
    source_system_canonical TEXT,
    canonical_confidence    TEXT CHECK (canonical_confidence IN ('high','medium','low')),
    -- Which data_asset (module) this table was attributed to, if any. This is the
    -- join that turns raw inventory into portfolio readiness signal.
    data_asset_id   INT REFERENCES data_assets(id) ON DELETE SET NULL,
    mapped_by       TEXT CHECK (mapped_by IN ('exact','normalized','llm','manual','lineage')),
    is_user_edited  BOOLEAN NOT NULL DEFAULT false,
    is_present      BOOLEAN NOT NULL DEFAULT true,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, catalog_name, schema_name, table_name)
);

CREATE INDEX IF NOT EXISTS idx_disc_tables_schema
    ON discovered_tables(workspace_id, catalog_name, schema_name);
CREATE INDEX IF NOT EXISTS idx_disc_tables_canonical
    ON discovered_tables(source_system_canonical);
CREATE INDEX IF NOT EXISTS idx_disc_tables_asset
    ON discovered_tables(data_asset_id);
-- Partial index: the enrichment job's candidate scan is always "not yet
-- enriched and still present", which is a shrinking minority of a large table.
CREATE INDEX IF NOT EXISTS idx_disc_tables_unenriched
    ON discovered_tables(id) WHERE ai_definition IS NULL AND is_present;

-- ---------------------------------------------------------------------------
-- Source-system alias resolution (raw label -> canonical)
-- ---------------------------------------------------------------------------
-- The persistent memo table for canonicalization. Keyed on the raw string so a
-- label only ever costs one LLM call across all runs, and so a human correction
-- is permanent.
CREATE TABLE IF NOT EXISTS data_asset_aliases (
    id              SERIAL PRIMARY KEY,
    raw             TEXT NOT NULL UNIQUE,
    -- lower(trim(raw)), maintained by the app for case-insensitive lookup.
    raw_normalized  TEXT NOT NULL,
    -- Resolved canonical source_category, or 'Other' when nothing fits.
    canonical       TEXT,
    -- How it was resolved. Ordered cheapest-to-most-expensive; see
    -- server/normalization.py for the cascade.
    mapped_by       TEXT NOT NULL CHECK (mapped_by IN
                        ('seed','exact','normalized','llm','manual','fallback_other')),
    confidence      TEXT CHECK (confidence IN ('high','medium','low')),
    notes           TEXT,
    -- mapped_by='manual' OR is_user_edited=true is NEVER overwritten by a re-run.
    is_user_edited  BOOLEAN NOT NULL DEFAULT false,
    mapped_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aliases_normalized ON data_asset_aliases(raw_normalized);
CREATE INDEX IF NOT EXISTS idx_aliases_canonical  ON data_asset_aliases(canonical);

-- ---------------------------------------------------------------------------
-- Asset taxonomy — effective-dated, closed-vocabulary classification
-- ---------------------------------------------------------------------------
-- Effective-dating (rather than UPDATE-in-place) means a reclassification keeps
-- its history: "this was T2 until we found it feeds the outage model". The
-- current value for a dimension is the row with effective_to IS NULL.
CREATE TABLE IF NOT EXISTS asset_taxonomy (
    id             SERIAL PRIMARY KEY,
    data_asset_id  INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    -- Closed set, mirrored in server/taxonomy.py DIMENSIONS. Deliberately only
    -- the three dimensions that add signal the rest of the model lacks:
    -- integration_pattern (how data arrives), criticality (how much it matters),
    -- vendor_type (build/buy/share posture). Domain/department/vertical are
    -- already covered by lobs, data_domains, and sub_vertical.
    dimension      TEXT NOT NULL CHECK (dimension IN
                       ('integration_pattern','criticality','vendor_type')),
    value          TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'ai'
                   CHECK (source IN ('ai','manual','catalog')),
    confidence     NUMERIC,
    ai_reasoning   TEXT,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_to   TIMESTAMPTZ,
    created_by     TEXT
);

-- One CURRENT value per (asset, dimension); history rows have effective_to set.
CREATE UNIQUE INDEX IF NOT EXISTS idx_taxonomy_current
    ON asset_taxonomy(data_asset_id, dimension) WHERE effective_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_taxonomy_dimension ON asset_taxonomy(dimension, value);

-- ---------------------------------------------------------------------------
-- Discovery-driven asset provenance
-- ---------------------------------------------------------------------------
-- When ingestion attributes real tables to a catalog module, record how many and
-- how confidently, so the UI can distinguish "curated because a human said so"
-- from "curated because we found 340 tables behind it".
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS discovered_table_count INT NOT NULL DEFAULT 0;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS discovery_confidence TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS last_discovered_at TIMESTAMPTZ;
