-- AI Value Flywheel schema — CHUNK L: use-case progression tracking (target
-- go-live dates, status/note history, slippage tracking).
-- Idempotent: safe to re-run.
--
-- THE FEATURE
-- -----------
-- Product leadership needs visibility into WHEN value may be realized (target go-live
-- dates), WHY timelines slip (date changes with reason), and centralized status/notes
-- for better tracking. All per-account scoped — one customer's target dates say
-- nothing about another's.
--
-- THE SCHEMA
-- ----------
-- Two tables:
--   1. account_use_case_progress: holds the CURRENT target_go_live_date per (account, use_case).
--      Updated in place when the target changes; previous values live in the history.
--   2. use_case_status_events: append-only log of changes (status transitions, date
--      moves with reason, free notes). Events WHERE event_type='date_change' AND
--      to_value > from_value are SLIPPAGE — the timeline moved later, which is what
--      execs need to see.
--
-- ACCOUNT SCOPING
-- ---------------
-- Every row carries account_id NOT NULL. There is no fallback to a shared catalog
-- column, because a go-live date is not a library fact — it is this customer's plan.
-- Queries MUST filter `account_id = $current_account` or they leak cross-tenant.
--
-- AT_RISK COMPUTATION
-- -------------------
-- A use case is at risk when its target_go_live_date is in the past and its status
-- is NOT {'live', 'value_realized'}. Computed server-side (routes/use_cases.py) to
-- avoid timezone drift between the DB and the client, and so the executive rollup
-- (dashboard, snapshots, roadmap) can aggregate it without re-reading every use case.

-- ---------------------------------------------------------------------------
-- Current progression state: target go-live date per (account, use case)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_use_case_progress (
    account_id         INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    use_case_id        INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    -- The CURRENT target go-live date. Null means no target set.
    target_go_live_date DATE,
    -- When this row was last written (date set, moved, or cleared).
    updated_at         TIMESTAMPTZ DEFAULT now(),
    updated_by         TEXT,
    PRIMARY KEY (account_id, use_case_id)
);

CREATE INDEX IF NOT EXISTS idx_uc_progress_target
    ON account_use_case_progress(account_id, target_go_live_date)
    WHERE target_go_live_date IS NOT NULL;

COMMENT ON TABLE account_use_case_progress IS 'Per-account target go-live dates for use cases (current state; history in use_case_status_events)';
COMMENT ON COLUMN account_use_case_progress.target_go_live_date IS 'The current target go-live date; null when no target set';

-- ---------------------------------------------------------------------------
-- Event history: status changes, date moves (incl. slippage), free notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS use_case_status_events (
    id             SERIAL PRIMARY KEY,
    account_id     INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    use_case_id    INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    -- Event types:
    --   'date_change': target_go_live_date moved (from_value/to_value are ISO dates)
    --   'date_set': initial target date set (from_value NULL, to_value is the date)
    --   'date_cleared': target date removed (from_value is the old date, to_value NULL)
    --   'status_change': use case status transition (from_value/to_value are status strings)
    --   'note': free-text note/update (from_value/to_value NULL, note carries the text)
    event_type     TEXT NOT NULL CHECK (event_type IN ('date_change', 'date_set', 'date_cleared', 'status_change', 'note')),
    -- Previous value (date as ISO string, or status string, or NULL for notes/initial set)
    from_value     TEXT,
    -- New value (date as ISO string, or status string, or NULL for cleared/notes)
    to_value       TEXT,
    -- Free-text reason/note. For date_change where to_value > from_value (slippage),
    -- this is the REQUIRED slippage reason. For 'note' events, this is the note body.
    note           TEXT,
    created_by     TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_uc_status_events_account_uc
    ON use_case_status_events(account_id, use_case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_uc_status_events_slippage
    ON use_case_status_events(account_id, use_case_id)
    WHERE event_type = 'date_change';

COMMENT ON TABLE use_case_status_events IS 'Append-only log of use-case progression events (date changes incl. slippage, status transitions, notes)';
COMMENT ON COLUMN use_case_status_events.event_type IS 'date_change (incl. slippage) | date_set | date_cleared | status_change | note';
COMMENT ON COLUMN use_case_status_events.note IS 'Reason for date change (required for slippage), or free-text note body for note events';
