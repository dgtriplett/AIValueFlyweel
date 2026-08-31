-- Migration 020: Add hypothesized value override columns (mirror realized_override_*)
--
-- The realized value editor has long supported two modes: CALCULATED (per-component
-- multipliers, live total) and OVERRIDE (straight dollar value + required note). This
-- migration adds identical override columns for HYPOTHESIZED value so both can be
-- edited the same way (drawer AND full-page detail).
--
-- These columns mirror realized_override_* exactly (see 001_init.sql:232-234). No new
-- constraints are needed beyond the table's existing account-scoped fail-closed model.
--
-- Deployment: columns only, additive, no SP grant needed. Safe to apply while running.

ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS hypothesized_override_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS hypothesized_override_amount NUMERIC;
ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS hypothesized_override_note TEXT;
