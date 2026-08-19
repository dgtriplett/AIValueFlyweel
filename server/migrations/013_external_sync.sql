-- Grid Atlas schema — CHUNK M: external app synchronization.
-- Tracks records imported from complementary roadmap/assessment apps so imports
-- are idempotent and do not fork duplicate use cases on each sync.

CREATE TABLE IF NOT EXISTS external_object_map (
    account_id          INT REFERENCES accounts(id) ON DELETE CASCADE,
    source_app          TEXT NOT NULL,
    source_object_type  TEXT NOT NULL,
    source_object_id    TEXT NOT NULL,
    local_object_type   TEXT NOT NULL,
    local_object_id     TEXT NOT NULL,
    source_hash         TEXT,
    imported_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_by         TEXT,
    PRIMARY KEY (
        account_id,
        source_app,
        source_object_type,
        source_object_id,
        local_object_type
    )
);

CREATE INDEX IF NOT EXISTS external_object_map_local
    ON external_object_map (local_object_type, local_object_id);
