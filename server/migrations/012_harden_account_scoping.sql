-- Grid Atlas schema — CHUNK K: harden customer-owned account scoping.
-- Idempotent: safe to re-run.
--
-- Migration 009 added account_id to several customer-owned tables but left it
-- nullable to allow genuinely shared rows in reference-like tables. That is right
-- for seeded KB folders and shared glossary terms, but not for rows that describe
-- a specific customer's work: value records, roadmap items, funding requests,
-- comments, research runs/proposals, and chat conversations.
--
-- Any NULL rows in those tables were created by older application code and were
-- therefore visible to every account. Adopt them into the default account, then
-- require future rows to carry an owner.

DO $$
DECLARE
    default_account INT;
    target TEXT;
    tables TEXT[] := ARRAY[
        'value_records',
        'roadmap_items',
        'funding_requests',
        'comments',
        'research_runs',
        'assumption_research',
        'chat_conversations'
    ];
BEGIN
    SELECT id INTO default_account FROM accounts WHERE is_default LIMIT 1;
    IF default_account IS NULL THEN
        RAISE NOTICE 'no default account; skipping account-scoping hardening';
        RETURN;
    END IF;

    FOREACH target IN ARRAY tables LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public'
                         AND table_name = target
                         AND column_name = 'account_id') THEN
            CONTINUE;
        END IF;

        EXECUTE format('UPDATE %I SET account_id = $1 WHERE account_id IS NULL', target)
            USING default_account;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN account_id SET NOT NULL', target);
    END LOOP;
END $$;

-- New accounts need their own baseline assumptions. Existing accounts that were
-- created after 009 but before the application-side seeding fix get a copy from
-- shared defaults when present, otherwise from the current default account. This
-- is a repair path; the app now uses scripts/seed_data.json for newly-created
-- accounts so it does not inherit a calibrated customer's numbers.
INSERT INTO value_assumptions
    (account_id, key, label, value, unit, category, source, source_note,
     confidence, updated_at)
SELECT a.id, src.key, src.label, src.value, src.unit, src.category,
       COALESCE(src.source, 'seed'), src.source_note, src.confidence, now()
FROM accounts a
CROSS JOIN LATERAL (
    SELECT DISTINCT ON (va.key)
           va.key, va.label, va.value, va.unit, va.category, va.source,
           va.source_note, va.confidence
    FROM value_assumptions va
    WHERE va.account_id IS NULL
       OR va.account_id = (SELECT id FROM accounts WHERE is_default LIMIT 1)
    ORDER BY va.key, (va.account_id IS NOT NULL)
) src
WHERE a.is_active
  AND NOT EXISTS (
      SELECT 1 FROM value_assumptions existing
      WHERE existing.account_id = a.id AND existing.key = src.key
  )
ON CONFLICT (account_id, key) DO NOTHING;
