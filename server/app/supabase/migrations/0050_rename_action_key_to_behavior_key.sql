-- 0050_rename_action_key_to_behavior_key.sql
-- Rename action_key column to behavior_key in jobs table for consistency with behaviors rename

BEGIN;

-- ============================================================================
-- Step 1: Rename column action_key -> behavior_key
-- ============================================================================

ALTER TABLE jobs RENAME COLUMN action_key TO behavior_key;

-- ============================================================================
-- Step 2: Rename index
-- ============================================================================

-- Drop old index and create with new name
DROP INDEX IF EXISTS idx_jobs_action;

CREATE INDEX idx_jobs_behavior
    ON jobs (companion_id, external_user_id, behavior_key)
    WHERE behavior_key IS NOT NULL;

-- ============================================================================
-- Step 3: Update comments
-- ============================================================================

COMMENT ON COLUMN jobs.behavior_key IS 'For behavior_execution jobs: the behavior key to execute';

COMMIT;
