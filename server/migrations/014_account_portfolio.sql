-- AI Value Flywheel schema — CHUNK N: account-owned portfolio membership.
-- Idempotent: safe to re-run.
--
-- use_cases is the shared catalog of possible work. The old in_portfolio column
-- was global, so creating a second account still showed the demo/default
-- portfolio. This table makes the selected portfolio account-owned while keeping
-- the catalog itself shared.

CREATE TABLE IF NOT EXISTS account_portfolio_use_cases (
    account_id   INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    use_case_id  INT NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    source       TEXT NOT NULL DEFAULT 'manual',
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, use_case_id)
);

CREATE INDEX IF NOT EXISTS account_portfolio_use_cases_uc
    ON account_portfolio_use_cases (use_case_id);

-- Preserve the current visible portfolio for the default account only. That keeps
-- the existing demo/default account intact but lets every newly-created account
-- start with a clean, empty portfolio.
INSERT INTO account_portfolio_use_cases
    (account_id, use_case_id, source, created_by)
SELECT a.id, uc.id, 'legacy_global_in_portfolio', 'migration_014'
FROM accounts a
CROSS JOIN use_cases uc
WHERE a.is_default
  AND uc.in_portfolio IS TRUE
ON CONFLICT (account_id, use_case_id) DO NOTHING;

-- Preserve real work that was already account-owned before this migration. These
-- rows should remain visible even for non-default accounts.
INSERT INTO account_portfolio_use_cases
    (account_id, use_case_id, source, created_by)
SELECT DISTINCT ri.account_id, ri.use_case_id, 'roadmap_item', 'migration_014'
FROM roadmap_items ri
WHERE ri.account_id IS NOT NULL
ON CONFLICT (account_id, use_case_id) DO NOTHING;

INSERT INTO account_portfolio_use_cases
    (account_id, use_case_id, source, created_by)
SELECT DISTINCT vr.account_id, vr.use_case_id, 'value_record', 'migration_014'
FROM value_records vr
WHERE vr.account_id IS NOT NULL
ON CONFLICT (account_id, use_case_id) DO NOTHING;

INSERT INTO account_portfolio_use_cases
    (account_id, use_case_id, source, created_by)
SELECT DISTINCT eom.account_id, eom.local_object_id::int, 'external_sync', 'migration_014'
FROM external_object_map eom
WHERE eom.account_id IS NOT NULL
  AND eom.local_object_type = 'use_case'
  AND eom.local_object_id ~ '^[0-9]+$'
ON CONFLICT (account_id, use_case_id) DO NOTHING;
