-- Add expose_status_events flag to companion_shares for public streaming controls

BEGIN;

ALTER TABLE companion_shares
    ADD COLUMN IF NOT EXISTS expose_status_events BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill existing rows to ensure deterministic default
UPDATE companion_shares
SET expose_status_events = COALESCE(expose_status_events, FALSE);

COMMIT;
