-- Grid Atlas schema — CHUNK F: instance branding.
-- Idempotent: safe to re-run.
--
-- Lets a deployment show the customer's own name and logo in the header, so the
-- app reads as theirs during a workshop rather than as a generic tool.
--
-- WHY THE LOGO BYTES LIVE IN LAKEBASE
-- -----------------------------------
-- The BHE original stored uploads in a Unity Catalog Volume. Here the bytes go in
-- Lakebase alongside the rest of the instance state, for three reasons:
--   1. It works on an install with no ATLAS_CATALOG — branding should not require
--      the discovery layer to be configured.
--   2. Serving through our own endpoint avoids the CORS/CSP surprises that come
--      with a public-internet image URL, and means the logo survives the source
--      URL going away.
--   3. A logo is a handful of KB. A bytea column is the whole feature; a Volume
--      plus a file-API round trip is machinery this does not need.
-- The size cap is enforced in the route, not here, so the limit can change without
-- a migration.

CREATE TABLE IF NOT EXISTS branding (
    id            INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- Overrides the header title. Defaults to the researched company name when
    -- blank, so a researched instance is branded without any extra step.
    display_name  TEXT,
    subtitle      TEXT,
    -- Accent colour as #RRGGBB. Validated in the route.
    accent_color  TEXT,
    logo_bytes    BYTEA,
    logo_mime     TEXT,
    logo_filename TEXT,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
