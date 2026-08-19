-- 0040_action_isolated_flag.sql
-- Adds 'isolated' flag to companion_action_links for controlling execution environment
--
-- When isolated=FALSE (default): Action runs in warm Modal Function (fast, ~100-300ms)
-- When isolated=TRUE: Action runs in isolated Modal Function (secure, ~300-500ms)
--   - Fresh container per request (no state leakage)
--   - Network access blocked
--   - Cannot call other Modal resources

BEGIN;

-- Add isolated flag to companion_action_links
-- Default FALSE = trusted path (faster, for verified developer actions)
ALTER TABLE companion_action_links
ADD COLUMN IF NOT EXISTS isolated BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN companion_action_links.isolated IS
'When TRUE, action runs in isolated container with no network/Modal access. Use for untrusted code. Default FALSE for fast execution.';

COMMIT;
