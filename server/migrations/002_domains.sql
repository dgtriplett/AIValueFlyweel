-- Grid Atlas schema — CHUNK B: the semantic data-domain layer.
-- Idempotent: safe to re-run.
--
-- WHY THIS LAYER EXISTS
-- ---------------------
-- CHUNK A binds a use case directly to a MODULE (a specific product subsystem)
-- via uc_requires_asset. That is precise but brittle: a utility running Maximo
-- instead of SAP PM technically fails a requirement it actually satisfies, and
-- the readiness story becomes "you don't have SAP Plant Maintenance" instead of
-- the useful "you need work-order history — and you already have two systems
-- that provide it".
--
-- The domain layer decouples WHAT data a use case needs from WHICH product
-- provides it:
--
--     use_case --requires--> data_domain <--serves-- data_asset (module)
--
-- A required domain is satisfied when ANY mapped asset is curated/governed, so
-- vendor substitution stops producing false gaps.
--
-- BACK-COMPAT CONTRACT (important)
-- -------------------------------
-- This is ADDITIVE. The 693 seeded uc_requires_asset edges, the deterministic
-- remap in scripts/derive_requires.py, and the manual-override lock
-- (use_cases.requires_locked + uc_requires_asset.manual) all keep working
-- untouched. server/readiness.py runs a DUAL PATH: it uses the domain layer for
-- a use case only when that use case actually has domain requirements, and
-- falls back to the direct module edges otherwise. A fresh install with an
-- unpopulated domain layer behaves exactly like CHUNK A.

-- ---------------------------------------------------------------------------
-- Data domains — the closed vocabulary of semantic data needs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_domains (
    id           SERIAL PRIMARY KEY,
    -- snake_case stable key, e.g. 'work_order_history'. This is what use cases
    -- and the LLM reference; renaming a label never breaks a mapping.
    name         TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL,
    description  TEXT,
    -- Coarse bucket for grouping in the UI. Deliberately P&U-shaped and aligned
    -- with the LOB/domain vocabulary the rest of the app already uses.
    category     TEXT CHECK (category IN (
                     'operational','asset','customer','grid','market',
                     'financial','regulatory','safety','external','workforce')),
    -- Comma-separated example fields, e.g. 'work_order_id, asset_id, completion_date'.
    -- Purely illustrative; helps a human confirm the domain means what they think.
    example_attributes TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT true,
    -- provenance, mirroring data_assets.origin: 'catalog' (shipped reference
    -- vocabulary), 'auto' (LLM-discovered), 'custom' (user-authored).
    origin       TEXT NOT NULL DEFAULT 'custom'
                 CHECK (origin IN ('catalog','auto','custom')),
    -- Never overwritten by the discovery/remap jobs once a human has edited it.
    is_user_edited BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- data_asset --serves--> data_domain  (M:N)
-- ---------------------------------------------------------------------------
-- Which modules can supply a given semantic need. Many-to-many in both
-- directions: one module serves several domains, and one domain is served by
-- several modules (which is exactly what kills the false-gap problem).
CREATE TABLE IF NOT EXISTS asset_serves_domain (
    data_asset_id  INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    domain_id      INT NOT NULL REFERENCES data_domains(id) ON DELETE CASCADE,
    confidence     TEXT NOT NULL DEFAULT 'high'
                   CHECK (confidence IN ('high','medium','low')),
    -- 'catalog' = shipped mapping, 'llm' = AI-proposed, 'manual' = human.
    mapped_by      TEXT NOT NULL DEFAULT 'catalog'
                   CHECK (mapped_by IN ('catalog','llm','manual')),
    notes          TEXT,
    is_user_edited BOOLEAN NOT NULL DEFAULT false,
    mapped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (data_asset_id, domain_id)
);

-- ---------------------------------------------------------------------------
-- use_case --requires--> data_domain
-- ---------------------------------------------------------------------------
-- The semantic mirror of uc_requires_asset. `necessity` intentionally uses the
-- same two-value shape as uc_requires_asset.criticality so readiness scoring
-- stays symmetric: 'required' drives readiness, 'helpful' is informational.
CREATE TABLE IF NOT EXISTS uc_requires_domain (
    use_case_id  INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    domain_id    INT NOT NULL REFERENCES data_domains(id) ON DELETE CASCADE,
    necessity    TEXT NOT NULL DEFAULT 'required'
                 CHECK (necessity IN ('required','helpful')),
    rationale    TEXT,
    -- 'catalog' = shipped, 'generation' = authored by the UC generation agent,
    -- 'extraction' = backfilled from an existing UC, 'manual' = human.
    mapped_by    TEXT NOT NULL DEFAULT 'catalog'
                 CHECK (mapped_by IN ('catalog','generation','extraction','manual')),
    manual       BOOLEAN NOT NULL DEFAULT false,
    mapped_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (use_case_id, domain_id)
);

-- Per-use-case lock, mirroring use_cases.requires_locked: when a human curates
-- a use case's domain requirements, the extraction agent must not clobber them.
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS domains_locked BOOLEAN NOT NULL DEFAULT false;

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- Reverse-direction lookups (the forward direction is served by each PK).
CREATE INDEX IF NOT EXISTS idx_asd_domain      ON asset_serves_domain(domain_id);
CREATE INDEX IF NOT EXISTS idx_urd_domain      ON uc_requires_domain(domain_id);
CREATE INDEX IF NOT EXISTS idx_domains_active  ON data_domains(is_active);
CREATE INDEX IF NOT EXISTS idx_domains_category ON data_domains(category);
