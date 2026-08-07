-- Grid Atlas schema — CHUNK H: enforce use-case title uniqueness in the database.
-- Idempotent: safe to re-run.
--
-- THE BUG THIS CLOSES
-- -------------------
-- routes/generate.py guards against creating a duplicate use case with a
-- check-then-insert:
--
--     SELECT id FROM use_cases WHERE lower(title) = lower($1)   -- clash?
--     if clash: continue
--     INSERT INTO use_cases ...
--
-- Nothing in the database backed that up, so the guard only held while requests
-- were serialized. Two concurrent commits both SELECT, both find no clash, and both
-- INSERT — the portfolio ends up with two identical use cases, each with its own
-- domain requirements and its own contribution to the portfolio total. The value
-- figures are then double-counted, which is exactly the kind of wrong number that
-- gets carried into a funding conversation.
--
-- It is reachable without concurrency too: a stale browser tab can issue a second
-- confirm token for the same preview. The propose step checks committed_at, but that
-- check happens before either token is consumed, so both tokens pass.
--
-- A unique index makes the invariant the database's job. The route keeps its
-- pre-check — it produces a clearer message and skips the row rather than failing
-- the whole batch — but correctness no longer depends on it winning a race.
--
-- WHY A FUNCTIONAL INDEX ON lower(title)
-- --------------------------------------
-- The route compares case-insensitively, so the constraint has to as well. A plain
-- UNIQUE (title) would happily accept "Outage Prediction" alongside "outage
-- prediction" and the duplicate would still appear twice in every rollup.

-- Pre-existing duplicates would make CREATE UNIQUE INDEX fail, and a migration that
-- aborts on real customer data is worse than one that reports what it found. Merge
-- them first: keep the lowest id (the original), and re-point every child row at it
-- so no requirement, value record, or roadmap item is orphaned.
DO $$
DECLARE
    duplicate_count INT;
BEGIN
    SELECT count(*) INTO duplicate_count FROM (
        SELECT lower(title) FROM use_cases
        GROUP BY lower(title) HAVING count(*) > 1
    ) AS dupes;

    IF duplicate_count = 0 THEN
        RAISE NOTICE 'no duplicate use-case titles';
        RETURN;
    END IF;

    RAISE NOTICE 'merging % duplicate use-case title group(s)', duplicate_count;

    -- Map every duplicate to the surviving (lowest) id for its title.
    CREATE TEMP TABLE uc_merge_map AS
    SELECT u.id AS dead_id, k.keep_id
    FROM use_cases u
    JOIN (SELECT lower(title) AS key, min(id) AS keep_id
          FROM use_cases GROUP BY lower(title)) k
      ON k.key = lower(u.title)
    WHERE u.id <> k.keep_id;

    -- Re-point children. ON CONFLICT DO NOTHING on the edge tables because the
    -- survivor may already have the same edge; the duplicate's copy is then simply
    -- dropped rather than colliding.
    INSERT INTO uc_requires_domain (use_case_id, domain_id, necessity, rationale, mapped_by)
    SELECT m.keep_id, r.domain_id, r.necessity, r.rationale, r.mapped_by
    FROM uc_requires_domain r JOIN uc_merge_map m ON m.dead_id = r.use_case_id
    ON CONFLICT (use_case_id, domain_id) DO NOTHING;

    INSERT INTO uc_requires_asset (use_case_id, data_asset_id, criticality)
    SELECT m.keep_id, r.data_asset_id, r.criticality
    FROM uc_requires_asset r JOIN uc_merge_map m ON m.dead_id = r.use_case_id
    ON CONFLICT DO NOTHING;

    UPDATE value_records v SET use_case_id = m.keep_id
    FROM uc_merge_map m WHERE v.use_case_id = m.dead_id;

    UPDATE roadmap_items ri SET use_case_id = m.keep_id
    FROM uc_merge_map m WHERE ri.use_case_id = m.dead_id;

    -- Record what was merged. An audit row is the only trace a customer will have
    -- that their use-case count changed during an upgrade.
    INSERT INTO audit_log (entity_type, entity_id, action, actor, diff_json)
    SELECT 'use_case', m.dead_id, 'merge_duplicate', 'migration_008',
           jsonb_build_object('merged_into', m.keep_id,
                              'reason', 'duplicate title; unique index added')
    FROM uc_merge_map m;

    -- Edges pointing at a dead id in either direction, then the rows themselves.
    DELETE FROM uc_enables_uc e USING uc_merge_map m
     WHERE e.from_use_case_id = m.dead_id OR e.to_use_case_id = m.dead_id;
    DELETE FROM use_cases u USING uc_merge_map m WHERE u.id = m.dead_id;

    DROP TABLE uc_merge_map;
END $$;

-- The invariant itself.
CREATE UNIQUE INDEX IF NOT EXISTS use_cases_title_unique
    ON use_cases (lower(title));
