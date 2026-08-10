-- AI Value Flywheel schema — CHUNK J: stop a new account inheriting another's
-- landed sources.
-- Idempotent: safe to re-run.
--
-- THE BUG
-- -------
-- 009 defined asset_status_by_account as
--
--     COALESCE(s.ingestion_status, da.ingestion_status, 'not_started')
--
-- intending the shared column as a fallback FOR THE DEFAULT ACCOUNT, so the upgrade
-- would be invisible. But the COALESCE applies to every account, and a newly created
-- account has no rows in account_asset_status at all — so it fell straight through to
-- the column and inherited the first customer's position.
--
-- Found by creating a second account on the live instance: 'National Grid' reported
-- the same 20 governed sources as Eversource, with zero differing. A new customer
-- would have opened the app and seen another utility's landed data as their own,
-- their readiness computed from it, and their portfolio value derived from that. It
-- is the exact cross-tenant leak the tenancy work existed to prevent, and nothing
-- would have surfaced it — the numbers look plausible.
--
-- THE FIX
-- -------
-- Give every account an explicit row for every asset, and drop the fallback. The view
-- then reads only from account_asset_status, so an account's position is whatever its
-- own rows say and nothing else. A missing row now means 'not_started', which is the
-- honest default for a customer who has told us nothing yet.
--
-- WHY NOT KEEP THE FALLBACK FOR JUST THE DEFAULT ACCOUNT
-- -----------------------------------------------------
-- It could be written as `CASE WHEN a.is_default THEN COALESCE(...)`, and that would
-- fix the leak while preserving 009's stated intent. But it makes the default account
-- behave differently from every other account, which is a rule someone has to know to
-- reason about any number the app shows. Backfilling rows is one migration; a
-- permanent asymmetry is a permanent tax.

-- ---------------------------------------------------------------------------
-- Backfill: every account gets a row for every asset
-- ---------------------------------------------------------------------------
-- The DEFAULT account adopts the shared column (preserving 009's upgrade path, which
-- was correct for it). Every other account starts at not_started, because we know
-- nothing about what they have landed.
INSERT INTO account_asset_status
    (account_id, data_asset_id, ingestion_status, updated_by)
SELECT a.id, da.id,
       CASE WHEN a.is_default
            THEN COALESCE(da.ingestion_status, 'not_started')
            ELSE 'not_started' END,
       'migration_010'
FROM accounts a
CROSS JOIN data_assets da
ON CONFLICT (account_id, data_asset_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- The view, without the cross-tenant fallback
-- ---------------------------------------------------------------------------
-- CROSS JOIN is kept so an asset added to the catalog after this migration still
-- appears for every account — it simply reads as not_started until that account says
-- otherwise, rather than vanishing from their coverage entirely.
--
-- The per-asset OVERRIDES (local_name, owning_lob_id, cost) still fall back to the
-- catalog, and that is correct: those are DESCRIPTIONS of a shared module, not
-- statements about one customer's position. A customer who has not renamed their OMS
-- should see it called "OMS (Outage Management)".
CREATE OR REPLACE VIEW asset_status_by_account AS
SELECT
    a.id                                    AS account_id,
    da.id                                   AS data_asset_id,
    -- No da.ingestion_status fallback. This is the fix.
    COALESCE(s.ingestion_status, 'not_started')        AS ingestion_status,
    COALESCE(s.local_name, da.module)                  AS display_name,
    COALESCE(s.owning_lob_id, da.owning_lob_id)        AS owning_lob_id,
    COALESCE(s.ingest_cost_low, da.ingest_cost_low)    AS ingest_cost_low,
    COALESCE(s.ingest_cost_high, da.ingest_cost_high)  AS ingest_cost_high,
    COALESCE(s.is_user_edited, false)                  AS is_user_edited,
    (s.account_id IS NOT NULL)                         AS has_account_row
FROM accounts a
CROSS JOIN data_assets da
LEFT JOIN account_asset_status s
       ON s.account_id = a.id AND s.data_asset_id = da.id;
