-- Grid Atlas schema — CHUNK E: company research + the remaining BHE-derived
-- features (rules, artifacts, glossary, chat).
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- Company profile
-- ---------------------------------------------------------------------------
-- One row. The utility this instance is about, as established by the research
-- agent (or typed in by hand). Everything the agent derives — LOBs, use cases,
-- and crucially the value assumptions — is grounded in these attributes, so they
-- are worth storing rather than re-prompting for.
CREATE TABLE IF NOT EXISTS company_profile (
    id                   INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    company_name         TEXT NOT NULL,
    -- Free-text but conventionally: IOU, municipal, co-op, G&T, generation-only.
    utility_type         TEXT,
    -- Which segments they operate. Drives which LOBs and use cases even apply.
    segments             TEXT[] DEFAULT '{}',   -- generation, transmission, distribution, retail
    service_territory    TEXT,
    regulator            TEXT,                  -- e.g. "MA DPU", "FERC + ISO-NE"
    iso_rto              TEXT,
    description          TEXT,
    -- Where the agent's numbers came from, so a reviewer can judge them.
    research_notes       TEXT,
    researched_at        TIMESTAMPTZ,
    researched_by        TEXT,
    model               TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Value-assumption provenance
-- ---------------------------------------------------------------------------
-- WHY THIS TABLE EXISTS
-- The 34 value assumptions drive every dollar figure in the app. Shipping them as
-- generic P&U defaults means every install quantifies value for a hypothetical
-- 2-million-customer utility, and the first question in any real conversation is
-- "where did these numbers come from?".
--
-- The research agent proposes calibrated values, and this records, per assumption:
-- what it was, what the agent proposed, HOW it got there, and how confident it is.
-- That turns "the app says $47M" into "the app says $47M because it assumed
-- 3.6M customers from the FERC Form 1 filing, which you can correct".
--
-- Kept separate from value_assumptions so the live value stays a single scalar the
-- engine reads without a join, and so history survives re-running the research.
CREATE TABLE IF NOT EXISTS assumption_research (
    id             SERIAL PRIMARY KEY,
    -- FK by value to value_assumptions.key. Not a hard FK: research may propose a
    -- key that a pruned install no longer has, and losing the row would lose the
    -- audit trail of what was proposed.
    key            TEXT NOT NULL,
    value_before   NUMERIC,
    value_proposed NUMERIC,
    -- 'high'   directly attested (a filing, a published figure the model recalls)
    -- 'medium' derived from an attested figure (per-customer ratio x customers)
    -- 'low'    industry-typical for this size/type, not company-specific
    confidence     TEXT CHECK (confidence IN ('high','medium','low')),
    -- The reasoning. Displayed verbatim next to the number, so it must stand alone.
    rationale      TEXT,
    -- What it was derived FROM, e.g. "FERC Form 1 2024", "10-K", "state commission
    -- rate case", "industry average for an IOU of this size".
    basis          TEXT,
    applied        BOOLEAN NOT NULL DEFAULT false,
    applied_at     TIMESTAMPTZ,
    research_run   INT,      -- groups a batch; see research_runs below
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assumption_research_key ON assumption_research(key);
CREATE INDEX IF NOT EXISTS idx_assumption_research_run ON assumption_research(research_run);

-- Batch bookkeeping, so a re-run is distinguishable from the first one and a
-- reviewer can see which numbers came from which pass.
CREATE TABLE IF NOT EXISTS research_runs (
    id            SERIAL PRIMARY KEY,
    company_name  TEXT NOT NULL,
    scope         TEXT[] DEFAULT '{}',   -- profile, assumptions, lobs, use_cases
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','partial')),
    stats_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings      TEXT[] DEFAULT '{}',
    model         TEXT,
    used_llm      BOOLEAN NOT NULL DEFAULT false,
    error         TEXT,
    actor         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

-- Record where a live assumption's value came from, so the UI can badge a
-- calibrated number differently from a shipped default.
ALTER TABLE value_assumptions ADD COLUMN IF NOT EXISTS source TEXT
    NOT NULL DEFAULT 'default';   -- default | research | manual
ALTER TABLE value_assumptions ADD COLUMN IF NOT EXISTS source_note TEXT;
ALTER TABLE value_assumptions ADD COLUMN IF NOT EXISTS confidence TEXT;
ALTER TABLE value_assumptions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Classification rules
-- ---------------------------------------------------------------------------
-- Every utility names its catalogs and schemas by some convention nobody wrote
-- down ("prod_", "_dw", an OpCo prefix). Rules let a user teach the app that
-- convention once, instead of hand-correcting thousands of discovered rows.
CREATE TABLE IF NOT EXISTS classification_rules (
    id           SERIAL PRIMARY KEY,
    -- What the rule decides.
    dimension    TEXT NOT NULL CHECK (dimension IN
                     ('environment','lob','source_category','zone','ignore')),
    -- Where to look.
    field        TEXT NOT NULL CHECK (field IN
                     ('catalog_name','schema_name','table_name','owner','comment')),
    -- How to match. 'regex' is offered because naming conventions are frequently
    -- positional in a way prefix matching cannot express.
    match_type   TEXT NOT NULL CHECK (match_type IN
                     ('equals','prefix','suffix','contains','regex')),
    pattern      TEXT NOT NULL,
    case_sensitive BOOLEAN NOT NULL DEFAULT false,
    -- What to assign when it matches. NULL for dimension='ignore'.
    value        TEXT,
    -- Lower runs first; the first match for a dimension wins, so a specific rule
    -- can be ordered ahead of a general one.
    priority     INT NOT NULL DEFAULT 100,
    is_active    BOOLEAN NOT NULL DEFAULT true,
    notes        TEXT,
    origin       TEXT NOT NULL DEFAULT 'manual'
                 CHECK (origin IN ('seed','manual','llm')),
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rules_dimension ON classification_rules(dimension, priority)
    WHERE is_active;

-- ---------------------------------------------------------------------------
-- BI / AI artifact inventory
-- ---------------------------------------------------------------------------
-- What has already been BUILT on the platform, as opposed to what data exists.
-- Distinct from linked_databricks_assets (which ties a specific artifact to a
-- specific use case): this is the full estate, most of which is unattributed —
-- and the unattributed part is the interesting bit, because it is work the
-- portfolio does not know about.
CREATE TABLE IF NOT EXISTS artifacts (
    id             SERIAL PRIMARY KEY,
    artifact_type  TEXT NOT NULL CHECK (artifact_type IN
                       ('dashboard','model','serving_endpoint','job','pipeline',
                        'query','genie_space','notebook','alert')),
    workspace_id   TEXT,
    artifact_id    TEXT,                  -- platform id, when there is one
    name           TEXT NOT NULL,
    owner          TEXT,
    description    TEXT,
    ai_description TEXT,
    uc_catalog     TEXT,
    uc_schema      TEXT,
    -- Activity signals, so a stale artifact is distinguishable from a live one.
    last_modified  TIMESTAMPTZ,
    last_run       TIMESTAMPTZ,
    run_count_30d  INT,
    status         TEXT,
    -- Attribution to the portfolio. NULL = nobody has claimed it.
    use_case_id    INT REFERENCES use_cases(id) ON DELETE SET NULL,
    lob_id         INT REFERENCES lobs(id) ON DELETE SET NULL,
    mapped_by      TEXT CHECK (mapped_by IN ('manual','llm','lineage')),
    is_user_edited BOOLEAN NOT NULL DEFAULT false,
    is_present     BOOLEAN NOT NULL DEFAULT true,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_type, workspace_id, artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_uc   ON artifacts(use_case_id);
-- The "unclaimed work" query, which is the point of the inventory.
CREATE INDEX IF NOT EXISTS idx_artifacts_unattributed ON artifacts(id)
    WHERE use_case_id IS NULL AND is_present;

-- ---------------------------------------------------------------------------
-- Glossary
-- ---------------------------------------------------------------------------
-- Business terms with the data behind them. Deliberately thin: it aggregates what
-- the domain/asset/LOB tables already know rather than introducing a parallel
-- vocabulary, because a second competing vocabulary is exactly the problem the
-- domain layer was built to avoid.
CREATE TABLE IF NOT EXISTS glossary_terms (
    id           SERIAL PRIMARY KEY,
    term         TEXT NOT NULL UNIQUE,
    definition   TEXT,
    -- Optional anchors into the model. A term usually IS a domain, but can be a
    -- looser business concept spanning several.
    domain_id    INT REFERENCES data_domains(id) ON DELETE SET NULL,
    lob_id       INT REFERENCES lobs(id) ON DELETE SET NULL,
    synonyms     TEXT[] DEFAULT '{}',
    -- Systems of record for this term, as free text (a name a business user would
    -- recognize) rather than FKs — the point is human legibility.
    source_systems TEXT[] DEFAULT '{}',
    owner        TEXT,
    origin       TEXT NOT NULL DEFAULT 'manual'
                 CHECK (origin IN ('catalog','derived','llm','manual')),
    is_user_edited BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_glossary_domain ON glossary_terms(domain_id);

-- ---------------------------------------------------------------------------
-- Chat
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_conversations (
    id          TEXT PRIMARY KEY,          -- 'conv_<random>'
    title       TEXT,
    actor       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
    content         TEXT,
    -- Tool calls and their results, so a reloaded conversation shows what the
    -- assistant actually did rather than only what it said.
    tool_name       TEXT,
    tool_args_json  JSONB,
    tool_result_json JSONB,
    -- Set when a turn produced a confirm card, so the card can be re-rendered.
    confirm_token   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
    ON chat_messages(conversation_id, created_at);
