-- AI Value Flywheel schema — CHUNK M: per-account use-case owner/assignee.
-- Idempotent: safe to re-run.
--
-- THE FEATURE
-- -----------
-- Portfolio managers need to assign an OWNER (or assignee) to each use case so it
-- is clear who is accountable for driving it to value. Ownership is a per-account
-- decision, NOT a library fact: the SAME catalog use case can live in several
-- accounts' portfolios at once, and each account assigns its own owner. There is
-- deliberately NO `use_cases.account_id` column — per-account state lives in
-- account-scoped tables (see migration 017's `account_use_case_progress`, which is
-- already keyed by (account_id, use_case_id)).
--
-- THE SCHEMA
-- ----------
-- Rather than create a new table, we reuse the existing account-scoped
-- `account_use_case_progress` table (introduced in migration 017) — it is already
-- keyed by (account_id, use_case_id) and is exactly the per-account overlay we need.
-- We add a single nullable `owner` text column (an email or display name). NULL means
-- "unassigned". Queries MUST filter `account_id = $current_account` or they leak
-- cross-tenant, same as every other column on this table.
--
-- SAFETY
-- ------
-- Additive only: ADD COLUMN IF NOT EXISTS on an existing table. No destructive DDL,
-- no data rewrite, no new stored-procedure grant needed (a column add on an already
-- granted table inherits the table's existing privileges).

ALTER TABLE account_use_case_progress
    ADD COLUMN IF NOT EXISTS owner TEXT;

COMMENT ON COLUMN account_use_case_progress.owner IS 'Per-account use-case owner/assignee (email or display name); NULL when unassigned';
