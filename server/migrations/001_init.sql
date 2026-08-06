-- GridValue schema (Lakebase Postgres, database "app")
-- CHUNK A: core portfolio / value / roadmap data model.
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- Lines of Business
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lobs (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT
);

-- ---------------------------------------------------------------------------
-- Data assets (source systems decomposed to the module level)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_assets (
    id               SERIAL PRIMARY KEY,
    -- Canonical generic system category (REQUIRED) — drives grouping.
    source_category  TEXT,
    -- Specific product (OPTIONAL metadata, user-set). Nullable.
    vendor           TEXT,
    -- Legacy display name; kept for back-compat, mirrors source_category.
    source_system    TEXT NOT NULL,
    module           TEXT NOT NULL,
    description      TEXT,
    sub_vertical     TEXT,
    ingestion_status TEXT NOT NULL DEFAULT 'not_started'
                     CHECK (ingestion_status IN ('not_started','landed','curated','governed')),
    -- Ingestion cost/effort to land this source (drives joint-funding ROI).
    ingest_effort    TEXT CHECK (ingest_effort IN ('S','M','L','XL')),
    ingest_cost_low  NUMERIC,   -- $ (absolute)
    ingest_cost_high NUMERIC,
    uc_catalog       TEXT,
    uc_schema        TEXT,
    owning_lob_id    INT REFERENCES lobs(id) ON DELETE SET NULL,
    -- provenance: 'catalog' (curated master list), 'auto' (inferred from a
    -- delivered use case), or 'custom' (user-added).
    origin           TEXT NOT NULL DEFAULT 'custom'
                     CHECK (origin IN ('catalog','auto','custom')),
    auto_captured    BOOLEAN NOT NULL DEFAULT false,
    auto_note        TEXT,
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Benefiting LOBs for a data asset (many-to-many) -> drives joint-funding
CREATE TABLE IF NOT EXISTS data_asset_lobs (
    data_asset_id INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    lob_id        INT NOT NULL REFERENCES lobs(id) ON DELETE CASCADE,
    PRIMARY KEY (data_asset_id, lob_id)
);

-- ---------------------------------------------------------------------------
-- Use cases
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS use_cases (
    id                     SERIAL PRIMARY KEY,
    title                  TEXT NOT NULL,
    description            TEXT,
    lob_id                 INT REFERENCES lobs(id) ON DELETE SET NULL,
    sub_vertical           TEXT CHECK (sub_vertical IN ('fossil','hydro','renewables','nuclear','cross')),
    stage                  TEXT CHECK (stage IN ('U1','U2','U3','U4','U5','U6')),
    -- Maturity phase / horizon wave (0 pre-foundation .. 3 mitigate risk) — drives
    -- blast-radius rings + roadmap sequencing. Distinct from project status below.
    phase                  INT CHECK (phase BETWEEN 0 AND 4),
    -- Project lifecycle status — separate axis, auto-reconciled from consumption
    -- in the live-integration chunk.
    status                 TEXT NOT NULL DEFAULT 'not_started'
                           CHECK (status IN ('not_started','scoping','in_progress','live','value_realized')),
    category               TEXT,
    effort_tshirt          TEXT CHECK (effort_tshirt IN ('S','M','L','XL')),
    priority_score         NUMERIC,
    risk_tags              TEXT[] DEFAULT '{}',
    compliance_tags        TEXT[] DEFAULT '{}',
    hypothesized_value_json JSONB,
    realized_value_amount  NUMERIC,
    -- Realized value, two ways (symmetric with hypothesized):
    --  1) parameterized: realized_value_json.components[] with actual multipliers,
    --     evaluated against the shared value_assumptions (same live-recompute).
    --  2) manual override: realized_override_* (calculated outside the app).
    realized_value_json    JSONB,
    realized_override_enabled BOOLEAN NOT NULL DEFAULT false,
    realized_override_amount  NUMERIC,
    realized_override_note    TEXT,
    status_source          TEXT NOT NULL DEFAULT 'manual'
                           CHECK (status_source IN ('manual','auto')),
    created_by             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Edges
-- ---------------------------------------------------------------------------
-- UseCase --requires--> DataAsset
CREATE TABLE IF NOT EXISTS uc_requires_asset (
    use_case_id   INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    data_asset_id INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    criticality   TEXT NOT NULL DEFAULT 'required'
                  CHECK (criticality IN ('required','helpful')),
    PRIMARY KEY (use_case_id, data_asset_id)
);

-- UseCase --enables--> UseCase (fast-follow chain)
CREATE TABLE IF NOT EXISTS uc_enables_uc (
    from_use_case_id  INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    to_use_case_id    INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    detected_by_agent BOOLEAN NOT NULL DEFAULT false,
    rationale         TEXT,
    PRIMARY KEY (from_use_case_id, to_use_case_id),
    CHECK (from_use_case_id <> to_use_case_id)
);

-- ---------------------------------------------------------------------------
-- Value records (hypothesized vs realized)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS value_records (
    id            SERIAL PRIMARY KEY,
    use_case_id   INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('hypothesized','realized')),
    metric_type   TEXT,
    amount        NUMERIC,
    unit          TEXT,
    fiscal_period TEXT,
    confidence    TEXT CHECK (confidence IN ('low','med','high')),
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Roadmap
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roadmap_items (
    id              SERIAL PRIMARY KEY,
    use_case_id     INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    horizon         TEXT CHECK (horizon IN ('now','next','later')),
    wave            INT,
    target_date     DATE,
    completion_date DATE,
    notes           TEXT
);

-- ---------------------------------------------------------------------------
-- Linked Databricks assets (for live reconcile in later chunks)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS linked_databricks_assets (
    id          SERIAL PRIMARY KEY,
    use_case_id INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    asset_type  TEXT CHECK (asset_type IN ('job','pipeline','model','dashboard')),
    asset_id    TEXT,
    asset_name  TEXT
);

-- ---------------------------------------------------------------------------
-- Comments (with @mentions)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comments (
    id          SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   INT NOT NULL,
    body        TEXT NOT NULL,
    author      TEXT,
    mentions    TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Funding requests (joint-funding conversations)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS funding_requests (
    id                SERIAL PRIMARY KEY,
    data_asset_id     INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    requesting_lob_id INT REFERENCES lobs(id) ON DELETE SET NULL,
    co_funding_lobs   INT[] DEFAULT '{}',
    combined_value    NUMERIC,
    status            TEXT DEFAULT 'proposed'
                      CHECK (status IN ('proposed','committed','funded','declined')),
    sponsor           TEXT,               -- champion driving the request
    cost_share_json   JSONB,              -- {lob_id: amount} proposed split
    brief_md          TEXT,               -- AI-generated funding brief (markdown)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Audit log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   INT,
    action      TEXT NOT NULL,
    actor       TEXT,
    diff_json   JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Benchmark library (reference value ranges for the value-estimator agent)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS benchmark_library (
    id               SERIAL PRIMARY KEY,
    use_case_pattern TEXT NOT NULL,
    sub_vertical     TEXT,
    metric_type      TEXT,
    low              NUMERIC,
    mid              NUMERIC,
    high             NUMERIC,
    unit             TEXT,
    notes            TEXT
);

-- ---------------------------------------------------------------------------
-- Idempotent column additions (for tables created by an earlier migration run)
-- ---------------------------------------------------------------------------
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS phase INT;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'not_started';
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'custom';
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS auto_captured BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS auto_note TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS source_category TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS vendor TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS ingest_effort TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS ingest_cost_low NUMERIC;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS ingest_cost_high NUMERIC;
ALTER TABLE funding_requests ADD COLUMN IF NOT EXISTS sponsor TEXT;
ALTER TABLE funding_requests ADD COLUMN IF NOT EXISTS cost_share_json JSONB;
ALTER TABLE funding_requests ADD COLUMN IF NOT EXISTS brief_md TEXT;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS realized_value_json JSONB;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS realized_override_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS realized_override_amount NUMERIC;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS realized_override_note TEXT;
-- Manual module-mapping override: when a user hand-edits a UC's required modules,
-- the UC is "locked" so the deterministic remap / agent auto-apply won't clobber it,
-- and each hand-added link is flagged manual for audit clarity.
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS requires_locked BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE uc_requires_asset ADD COLUMN IF NOT EXISTS manual BOOLEAN NOT NULL DEFAULT false;
-- Catalog vs Portfolio split: the shipped 240 use cases are a predefined CATALOG
-- (origin='catalog'); the active PORTFOLIO is the customer's confirmed picks +
-- their own. Custom-authored UCs are origin='custom', in_portfolio=true.
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'custom';
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS in_portfolio BOOLEAN NOT NULL DEFAULT true;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'use_cases_origin_check') THEN
    ALTER TABLE use_cases ADD CONSTRAINT use_cases_origin_check
      CHECK (origin IN ('catalog','custom','auto'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'use_cases_status_check') THEN
    ALTER TABLE use_cases ADD CONSTRAINT use_cases_status_check
      CHECK (status IN ('not_started','scoping','in_progress','live','value_realized'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'use_cases_phase_check') THEN
    ALTER TABLE use_cases ADD CONSTRAINT use_cases_phase_check CHECK (phase BETWEEN 0 AND 4);
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Value assumptions (global, adjustable) — drive the parameterized value model
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS value_assumptions (
    id          SERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    value       NUMERIC NOT NULL,
    unit        TEXT,
    category    TEXT,
    description TEXT
);

-- ---------------------------------------------------------------------------
-- Helpful indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_use_cases_lob        ON use_cases(lob_id);
CREATE INDEX IF NOT EXISTS idx_use_cases_stage      ON use_cases(stage);
CREATE INDEX IF NOT EXISTS idx_data_assets_lob      ON data_assets(owning_lob_id);
CREATE INDEX IF NOT EXISTS idx_data_assets_status   ON data_assets(ingestion_status);
CREATE INDEX IF NOT EXISTS idx_ura_asset            ON uc_requires_asset(data_asset_id);
CREATE INDEX IF NOT EXISTS idx_enables_to           ON uc_enables_uc(to_use_case_id);
CREATE INDEX IF NOT EXISTS idx_value_uc             ON value_records(use_case_id);
CREATE INDEX IF NOT EXISTS idx_roadmap_uc           ON roadmap_items(use_case_id);
CREATE INDEX IF NOT EXISTS idx_comments_entity      ON comments(entity_type, entity_id);
