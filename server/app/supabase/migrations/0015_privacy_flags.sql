-- 0015_privacy_flags.sql
-- Add persistent flags for privacy mode on conversations

BEGIN;

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS privacy_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS privacy_last_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN conversations.privacy_enabled IS 'If true, Privacy Mode is enabled (persisted) for this conversation in Analytics UI.';
COMMENT ON COLUMN conversations.privacy_last_computed_at IS 'Timestamp when redactions were last computed successfully.';

COMMIT;
