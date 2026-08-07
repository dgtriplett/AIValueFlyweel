-- Grid Atlas schema — CHUNK I: accounts (multi-tenancy).
-- Idempotent: safe to re-run.
--
-- THE PROBLEM
-- -----------
-- `company_profile` and `branding` were `id INT PRIMARY KEY CHECK (id = 1)` — a
-- deliberate single-row singleton. So one deployment served exactly one utility, and
-- running Grid Atlas for a territory meant one app, one Lakebase project, and one set
-- of GRANTs per customer. That is the difference between a tool you demo and a
-- product you sell.
--
-- WHAT IS SCOPED AND WHAT IS NOT
-- ------------------------------
-- Scoped to an account (the customer's own answers):
--   company_profile, branding, value_assumptions, research_runs,
--   assumption_research, kb_* , glossary_terms, chat_*, and — most importantly —
--   which sources they have actually landed.
--
-- Shared reference data (the library the product ships):
--   use_cases, data_assets, data_domains, uc_requires_domain, asset_serves_domain,
--   uc_enables_uc, lobs.
--
-- The reason for the split: the 239 use cases and 146 source modules are the
-- product's intellectual property, curated once and improved for everyone. Copying
-- them per account would mean a catalog fix has to be applied N times and every
-- account slowly diverges from the library. The customer-specific part is not the
-- catalog, it is their POSITION against it.
--
-- THE HARD PART: ingestion_status
-- ------------------------------
-- `data_assets.ingestion_status` is per-CUSTOMER state sitting on a SHARED table.
-- Eversource having landed their OMS says nothing about National Grid. Adding
-- account_id to data_assets would fork the catalog — exactly what the split above
-- avoids — so the status moves to its own scoped table and the column becomes a
-- fallback for the default account.
--
-- Readiness reads through a view that resolves per-account status with the column as
-- a default, so every existing query keeps working while the new table takes over.

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id           SERIAL PRIMARY KEY,
    -- Short identifier used in URLs and in the account switcher.
    slug         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    -- Free text: 'investor-owned', 'municipal', 'cooperative', 'generation-only'.
    -- Not a CHECK because the taxonomy of utility ownership is genuinely messy and a
    -- constraint here would block a legitimate customer at insert time.
    utility_type TEXT,
    -- Archived accounts stay for the record but drop out of the switcher.
    is_active    BOOLEAN NOT NULL DEFAULT true,
    -- Exactly one account is the default: the one an unscoped request resolves to.
    -- Enforced by the partial unique index below rather than by application code,
    -- because "which account am I" must never be ambiguous.
    is_default   BOOLEAN NOT NULL DEFAULT false,
    notes        TEXT,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one default. A second one would make an unscoped read nondeterministic —
-- the portfolio total would change depending on row order.
CREATE UNIQUE INDEX IF NOT EXISTS accounts_single_default
    ON accounts (is_default) WHERE is_default;
CREATE INDEX IF NOT EXISTS accounts_active ON accounts (is_active);

-- The migration path for an existing install. Everything already in the database
-- belongs to one customer, so create their account and adopt the existing rows into
-- it. Named from the researched company profile when there is one, so an upgrade
-- lands on "Eversource Energy" rather than "Default".
INSERT INTO accounts (slug, name, utility_type, is_default, created_by)
SELECT
    -- A slug from the company name, or 'default' when there is no profile yet.
    COALESCE(
        NULLIF(regexp_replace(lower(COALESCE(cp.company_name, '')),
                              '[^a-z0-9]+', '-', 'g'), ''),
        'default'),
    COALESCE(cp.company_name, 'Default account'),
    cp.utility_type,
    true,
    'migration_009'
FROM (SELECT 1) AS one
LEFT JOIN company_profile cp ON cp.id = 1
WHERE NOT EXISTS (SELECT 1 FROM accounts)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Add account_id to the customer-specific tables
-- ---------------------------------------------------------------------------
-- Each is added nullable, backfilled to the default account, then indexed. NOT NULL
-- is deliberately NOT set: a nullable account_id means "global / unscoped", which is
-- what lets a shared row (a seeded KB folder, say) serve every account without being
-- duplicated. The read path treats NULL as "visible to all".
DO $$
DECLARE
    default_account INT;
    target TEXT;
    tables TEXT[] := ARRAY[
        'company_profile', 'branding', 'value_assumptions', 'research_runs',
        'assumption_research', 'kb_articles', 'kb_folders', 'glossary_terms',
        'chat_conversations', 'value_records', 'roadmap_items',
        'funding_requests', 'comments'
    ];
BEGIN
    SELECT id INTO default_account FROM accounts WHERE is_default LIMIT 1;
    IF default_account IS NULL THEN
        RAISE NOTICE 'no default account; skipping backfill';
        RETURN;
    END IF;

    FOREACH target IN ARRAY tables LOOP
        -- Skip tables this install does not have (an older schema, or a table added
        -- by a later migration on a database being upgraded out of order).
        IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = target) THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS account_id INT '
            'REFERENCES accounts(id) ON DELETE CASCADE', target);
        EXECUTE format(
            'UPDATE %I SET account_id = $1 WHERE account_id IS NULL', target)
            USING default_account;
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I (account_id)',
            target || '_account', target);
    END LOOP;
END $$;

-- The singleton CHECK constraints have to go, or a second account cannot have a
-- profile. Dropped by name with IF EXISTS so a re-run is a no-op.
ALTER TABLE company_profile DROP CONSTRAINT IF EXISTS company_profile_id_check;
ALTER TABLE branding DROP CONSTRAINT IF EXISTS branding_id_check;

-- One profile and one branding row per account. This replaces the id=1 singleton
-- with the constraint that was actually intended: unique PER TENANT.
CREATE UNIQUE INDEX IF NOT EXISTS company_profile_one_per_account
    ON company_profile (account_id);
CREATE UNIQUE INDEX IF NOT EXISTS branding_one_per_account
    ON branding (account_id);

-- value_assumptions was keyed on `key` alone, which would collide the moment a
-- second account calibrated the same assumption differently.
ALTER TABLE value_assumptions DROP CONSTRAINT IF EXISTS value_assumptions_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS value_assumptions_key_per_account
    ON value_assumptions (account_id, key);

-- ---------------------------------------------------------------------------
-- Per-account source status
-- ---------------------------------------------------------------------------
-- The table that makes the tenancy real. `data_assets.ingestion_status` stays as the
-- default-account fallback so nothing existing breaks, and this holds each account's
-- own position against the shared catalog.
CREATE TABLE IF NOT EXISTS account_asset_status (
    account_id       INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    data_asset_id    INT NOT NULL REFERENCES data_assets(id) ON DELETE CASCADE,
    -- Same vocabulary as data_assets.ingestion_status.
    ingestion_status TEXT NOT NULL DEFAULT 'not_started',
    -- Per-account overrides for the things that differ by customer even when the
    -- module is the same: what they call it, which line of business owns it, and
    -- what it costs THEM to land (a quote is customer-specific).
    local_name       TEXT,
    owning_lob_id    INT REFERENCES lobs(id) ON DELETE SET NULL,
    ingest_cost_low  NUMERIC,
    ingest_cost_high NUMERIC,
    notes            TEXT,
    -- A human set this, so a re-sweep must not overwrite it. Same rule as the rest
    -- of the app: a person's decision outranks a detector's.
    is_user_edited   BOOLEAN NOT NULL DEFAULT false,
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, data_asset_id)
);
CREATE INDEX IF NOT EXISTS account_asset_status_account
    ON account_asset_status (account_id, ingestion_status);

-- Adopt the existing statuses into the default account, so the upgrade is invisible:
-- the same 50 landed and 20 governed sources come back for that account.
INSERT INTO account_asset_status
    (account_id, data_asset_id, ingestion_status, updated_by)
SELECT a.id, da.id, da.ingestion_status, 'migration_009'
FROM accounts a
CROSS JOIN data_assets da
WHERE a.is_default
ON CONFLICT (account_id, data_asset_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- The resolution view
-- ---------------------------------------------------------------------------
-- Every readiness query needs "this account's status for this asset". A view keeps
-- that logic in ONE place: without it, the coalesce would be copy-pasted into the
-- readiness query, four domain queries, the flow Sankey, and both recommenders — and
-- the seventh copy would be the one that disagreed.
--
-- COALESCE order matters: the account's own row wins, and the shared column is the
-- fallback. An account with no row for an asset therefore inherits the seeded
-- default rather than silently reading as not_started.
CREATE OR REPLACE VIEW asset_status_by_account AS
SELECT
    a.id                                   AS account_id,
    da.id                                  AS data_asset_id,
    COALESCE(s.ingestion_status, da.ingestion_status, 'not_started')
                                           AS ingestion_status,
    COALESCE(s.local_name, da.module)       AS display_name,
    COALESCE(s.owning_lob_id, da.owning_lob_id)       AS owning_lob_id,
    COALESCE(s.ingest_cost_low, da.ingest_cost_low)   AS ingest_cost_low,
    COALESCE(s.ingest_cost_high, da.ingest_cost_high) AS ingest_cost_high,
    COALESCE(s.is_user_edited, false)       AS is_user_edited,
    (s.account_id IS NOT NULL)              AS has_account_row
FROM accounts a
CROSS JOIN data_assets da
LEFT JOIN account_asset_status s
       ON s.account_id = a.id AND s.data_asset_id = da.id;
