-- AI Value Flywheel schema — CHUNK K: asset detail enrichment + rationale for
-- uc_requires_asset edges.
-- Idempotent: safe to re-run.
--
-- PART 1: Enrich data_assets with four fields that describe what an asset provides
-- and how it is governed:
--   - provides: narrative "what does this module give you?"
--   - refresh_cadence: how often the data updates (e.g. "15-minute", "daily", "on-demand")
--   - steward: who owns the data (team/person)
--   - source_of_record: canonical system for this domain
--
-- PART 2: Add `rationale` to uc_requires_asset, making it symmetric with uc_requires_domain.
-- A domain has a rationale ("why this semantic need"), but the module edges did not — so
-- a user looking at a required asset saw criticality but not *why* that asset is required
-- for THIS use case.

ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS provides TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS refresh_cadence TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS steward TEXT;
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS source_of_record TEXT;

ALTER TABLE uc_requires_asset ADD COLUMN IF NOT EXISTS rationale TEXT;

COMMENT ON COLUMN data_assets.provides IS 'Narrative description of what this module provides (business capabilities / data it contains)';
COMMENT ON COLUMN data_assets.refresh_cadence IS 'How often the data updates (e.g. 15-minute, daily, on-demand)';
COMMENT ON COLUMN data_assets.steward IS 'Who owns/governs this data (team or person)';
COMMENT ON COLUMN data_assets.source_of_record IS 'The canonical system for this data domain';
COMMENT ON COLUMN uc_requires_asset.rationale IS 'Why this specific asset is required for this use case (symmetric with uc_requires_domain.rationale)';
