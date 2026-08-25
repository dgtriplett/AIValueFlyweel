-- Add app_users table for user roles (Phase A: admin-portal/roles foundation).
--
-- This is the source of truth for a user's stored role/persona. The role here
-- INFERS persona (not a self-selected dropdown), except:
--   * GRID_ATLAS_ADMINS allowlist ALWAYS grants 'admin' (bootstrap / lockout-proof).
--   * Admins may temporarily 'view as' another persona (later phase).
--
-- This is an ADDITIVE migration creating a NEW TABLE — it does not alter any
-- existing schema. The app service principal NEEDS a grant on this table
-- (migration runner must call with --grant-app-sp).
--
-- NO DESTRUCTIVE DDL: idempotent, safe to re-run.

CREATE TABLE IF NOT EXISTS app_users (
  email        TEXT PRIMARY KEY,
  role         TEXT NOT NULL DEFAULT 'pm' CHECK (role IN ('admin','pm','executive')),
  granted_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for role-based filtering (later admin UI: list executives, list PMs).
CREATE INDEX IF NOT EXISTS idx_app_users_role ON app_users(role);

-- Comment explaining the trust boundary:
-- GRID_ATLAS_ADMINS env allowlist ALWAYS grants admin (bootstrap, lockout-proof).
-- This table holds granted roles for everyone else; admins manage them in a later phase.
COMMENT ON TABLE app_users IS 'User roles for persona inference. GRID_ATLAS_ADMINS allowlist always grants admin (bootstrap); this holds granted roles for everyone else. Admins manage these in later phases.';
