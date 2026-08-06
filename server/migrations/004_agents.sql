-- Grid Atlas schema — CHUNK D: agent authoring support.
-- Idempotent: safe to re-run.
--
-- Two tables backing the use-case generation agent:
--
--   confirm_tokens        the propose/confirm gate for agent-initiated writes.
--                        See server/confirm.py for why the payload is stored
--                        server-side and why tokens are single-use.
--
--   uc_generation_previews  batches of generated candidates awaiting selection.
--                        Generation is expensive; a preview lets the user pick a
--                        subset without re-running it.
--
-- Both are ephemeral working state with a TTL, not portfolio data. Neither is
-- required for the app to serve the portfolio.

-- ---------------------------------------------------------------------------
-- Confirm tokens
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS confirm_tokens (
    token        TEXT PRIMARY KEY,
    intent       TEXT NOT NULL,
    -- What the executor will actually write. The client never resends this.
    payload_json JSONB NOT NULL,
    -- Display-only diff halves, so a rendering change can't alter the write.
    before_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary      TEXT,
    actor        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    -- Non-NULL means claimed. The atomic claim in consume_token() keys on this
    -- being NULL, which is what makes a replayed confirm a no-op.
    consumed_at  TIMESTAMPTZ,
    consumed_by  TEXT
);

-- Supports the "clean up old tokens" sweep and audit queries by recency.
CREATE INDEX IF NOT EXISTS idx_confirm_tokens_expiry ON confirm_tokens(expires_at);

-- ---------------------------------------------------------------------------
-- Use-case generation previews
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS uc_generation_previews (
    id             TEXT PRIMARY KEY,          -- 'prev_<random>'
    -- The request that produced this batch, echoed back so the UI can label it
    -- and so a repeat request is recognizable.
    lob_id         INT REFERENCES lobs(id) ON DELETE SET NULL,
    lens           TEXT CHECK (lens IN ('ready','gap','both')),
    request_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The candidates themselves: [{candidate_id, title, description, ...}, ...].
    candidates_json JSONB NOT NULL,
    -- Whether the model or the heuristic fallback produced it, for transparency.
    model          TEXT,
    used_llm       BOOLEAN NOT NULL DEFAULT false,
    actor          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ NOT NULL,
    committed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_uc_previews_expiry ON uc_generation_previews(expires_at);

-- ---------------------------------------------------------------------------
-- Generated-use-case provenance
-- ---------------------------------------------------------------------------
-- Where a use case came from, so the portfolio can distinguish "we wrote this"
-- from "the agent proposed it and we accepted". `origin` already covers
-- catalog/custom/auto; this records the generating batch for traceability.
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS generated_from_preview TEXT;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS generation_lens TEXT;
