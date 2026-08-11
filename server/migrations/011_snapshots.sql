-- AI Value Flywheel schema — CHUNK K: portfolio snapshots over time.
-- Idempotent: safe to re-run.
--
-- WHY
-- ---
-- Every number the app shows is instantaneous. A customer six months into a programme
-- can see that 35 domains are covered and $3.5B is buildable, but not that they
-- started at 12 and $1.6B — which is the only version of the story that shows the
-- programme working. "Here is where you are" is a status report; "here is your
-- trajectory" is the thing that renews funding.
--
-- WHAT A SNAPSHOT IS
-- ------------------
-- A denormalized row: the portfolio's headline figures at a moment, per account. It
-- deliberately does NOT reference use_cases or data_assets. A snapshot has to remain
-- readable after a use case is renamed, a source is re-scoped, or an assumption is
-- recalibrated — otherwise the history silently rewrites itself and the trend becomes
-- meaningless. This is a ledger, not a view.
--
-- WHY EVENT-TRIGGERED AND NOT SCHEDULED
-- -------------------------------------
-- A daily job would produce mostly identical rows and needs its own resource, its own
-- failure mode, and its own alerting. Snapshots are instead captured when something
-- that MOVES the number is confirmed — assumptions applied, a source landed, a use
-- case status changed — plus a manual button. Every point on the chart then
-- corresponds to a real event, and `reason` says which, so the chart explains itself
-- rather than needing a separate changelog.

CREATE TABLE IF NOT EXISTS value_snapshots (
    id                  SERIAL PRIMARY KEY,
    account_id          INT REFERENCES accounts(id) ON DELETE CASCADE,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- What moved the number. Free text rather than a CHECK: the set of things worth
    -- recording will grow, and a constraint here would mean a migration every time a
    -- new trigger is added. The values used today are 'manual', 'assumptions_applied',
    -- 'source_landed', 'status_changed', 'research_applied', 'seed'.
    reason              TEXT NOT NULL DEFAULT 'manual',
    -- Free-form context: which source, which use case, who confirmed it.
    detail              TEXT,
    captured_by         TEXT,

    -- Value, in $M/year at full run-rate.
    total_value_mm      NUMERIC,
    buildable_value_mm  NUMERIC,   -- shovel-ready only: what you could start now
    realized_value_mm   NUMERIC,

    -- Readiness distribution. The four states readiness.classify() produces.
    shovel_ready        INT,
    nearly_ready        INT,
    awaiting_prereqs    INT,
    blocked             INT,

    -- Data position.
    sources_total       INT,
    sources_ready       INT,
    domains_total       INT,
    domains_satisfied   INT,

    -- Portfolio shape.
    use_cases_total     INT,
    use_cases_live      INT,

    -- The full computed payload, for anything not broken out into a column above.
    -- Kept so a later chart can plot something this schema did not anticipate without
    -- needing a migration AND a backfill that cannot be done — the past is gone.
    metrics_json        JSONB
);

CREATE INDEX IF NOT EXISTS value_snapshots_account_time
    ON value_snapshots (account_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS value_snapshots_reason
    ON value_snapshots (reason);

-- One snapshot per account per minute per reason, at most. Without this, confirming a
-- batch of twenty assumption changes writes twenty rows a few milliseconds apart and
-- the chart becomes a vertical line at one timestamp instead of a trend. The route
-- checks this too, for a clearer message; the index is what makes it true under
-- concurrency.
--
-- Via a GENERATED column rather than a functional index on date_trunc(): Postgres
-- rejects that with "functions in index expression must be marked IMMUTABLE", because
-- date_trunc on a timestamptz depends on the session TimeZone and so is only STABLE.
-- Casting to `timestamp` first removes the timezone dependence, which makes the
-- expression immutable and indexable.
ALTER TABLE value_snapshots
    ADD COLUMN IF NOT EXISTS captured_minute TIMESTAMP
    GENERATED ALWAYS AS (date_trunc('minute', captured_at AT TIME ZONE 'UTC')) STORED;

CREATE UNIQUE INDEX IF NOT EXISTS value_snapshots_one_per_minute
    ON value_snapshots (account_id, captured_minute, reason);
