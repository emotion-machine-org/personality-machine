-- 0054_fix_jobs_unique_constraint.sql
-- Fix overly restrictive unique constraint on jobs table.
--
-- Problem: idx_jobs_unique_active_companion prevents a companion from having
-- more than one active behavior_execution job at a time. This breaks legitimate
-- scenarios where a companion serves multiple users concurrently.
--
-- Solution: Drop the constraint. Application code already handles deduplication
-- for specific cases (cron jobs, idle triggers) by checking params like
-- behavior_key, relationship_id, and trigger_source.

BEGIN;

-- Drop the overly restrictive constraint
DROP INDEX IF EXISTS idx_jobs_unique_active_companion;

-- Note: We intentionally do NOT add a replacement constraint.
-- The application code in modal_behavior_executor.py already handles
-- deduplication for scheduled behaviors by checking:
--   - params->>'behavior_key'
--   - params->>'relationship_id'
--   - params->>'trigger_source'
-- This allows proper concurrent behavior execution while preventing
-- true duplicates (same behavior for same relationship from same trigger).

COMMIT;
