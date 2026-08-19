-- 0049_rename_actions_to_behaviors.sql
-- API v2: Rename actions -> behaviors, companion_action_links -> companion_behavior_links
-- Also adds relationship_id for per-relationship behavior config and webhook_secret

BEGIN;

-- ============================================================================
-- Step 1: Rename actions table -> behaviors
-- ============================================================================

ALTER TABLE actions RENAME TO behaviors;

-- Rename indexes
ALTER INDEX IF EXISTS idx_actions_project RENAME TO idx_behaviors_project;

-- Rename constraints
ALTER TABLE behaviors RENAME CONSTRAINT actions_project_key_unique TO behaviors_project_key_unique;

-- ============================================================================
-- Step 2: Rename companion_action_links table -> companion_behavior_links
-- ============================================================================

ALTER TABLE companion_action_links RENAME TO companion_behavior_links;

-- Rename indexes
ALTER INDEX IF EXISTS idx_companion_action_links_companion RENAME TO idx_companion_behavior_links_companion;
ALTER INDEX IF EXISTS idx_companion_action_links_classifier RENAME TO idx_companion_behavior_links_classifier;

-- Rename column action_id -> behavior_id
ALTER TABLE companion_behavior_links RENAME COLUMN action_id TO behavior_id;

-- ============================================================================
-- Step 3: Add relationship_id for per-relationship behavior config
-- ============================================================================

-- When NULL: companion-level config (applies to all relationships)
-- When set: relationship-specific override
ALTER TABLE companion_behavior_links
    ADD COLUMN IF NOT EXISTS relationship_id UUID REFERENCES relationships(id) ON DELETE CASCADE;

-- ============================================================================
-- Step 4: Add webhook_secret for signature verification
-- ============================================================================

ALTER TABLE companion_behavior_links
    ADD COLUMN IF NOT EXISTS webhook_secret TEXT;

-- ============================================================================
-- Step 5: Update constraints
-- ============================================================================

-- Drop old FK and unique constraints
ALTER TABLE companion_behavior_links
    DROP CONSTRAINT IF EXISTS companion_action_links_action_id_fkey;

ALTER TABLE companion_behavior_links
    DROP CONSTRAINT IF EXISTS companion_action_links_companion_id_action_id_key;

-- Add new FK
ALTER TABLE companion_behavior_links
    ADD CONSTRAINT companion_behavior_links_behavior_id_fkey
    FOREIGN KEY (behavior_id) REFERENCES behaviors(id) ON DELETE CASCADE;

-- New unique constraint: one config per (companion, behavior, relationship) tuple
-- relationship_id can be NULL (companion-level default)
CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_behavior_links_unique
    ON companion_behavior_links(companion_id, behavior_id, COALESCE(relationship_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- ============================================================================
-- Step 6: Add indexes for relationship-level lookups
-- ============================================================================

-- Find behavior configs for a specific relationship
CREATE INDEX IF NOT EXISTS idx_companion_behavior_links_relationship
    ON companion_behavior_links(relationship_id)
    WHERE relationship_id IS NOT NULL AND enabled = TRUE;

-- Find all configs (companion + relationship level) for a companion
CREATE INDEX IF NOT EXISTS idx_companion_behavior_links_companion_all
    ON companion_behavior_links(companion_id, relationship_id NULLS FIRST)
    WHERE enabled = TRUE;

-- ============================================================================
-- Step 7: Update triggers
-- ============================================================================

-- Drop old triggers
DROP TRIGGER IF EXISTS trg_actions_updated_at ON behaviors;
DROP TRIGGER IF EXISTS trg_actions_version ON behaviors;
DROP TRIGGER IF EXISTS trg_companion_action_links_updated_at ON companion_behavior_links;

-- Recreate triggers with new names
CREATE TRIGGER trg_behaviors_updated_at
    BEFORE UPDATE ON behaviors
    FOR EACH ROW
    EXECUTE FUNCTION touch_jobs_updated_at();

CREATE TRIGGER trg_companion_behavior_links_updated_at
    BEFORE UPDATE ON companion_behavior_links
    FOR EACH ROW
    EXECUTE FUNCTION touch_jobs_updated_at();

-- ============================================================================
-- Step 8: Update version increment function
-- ============================================================================

CREATE OR REPLACE FUNCTION increment_behavior_version()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.source_code IS DISTINCT FROM OLD.source_code
       OR NEW.dependencies IS DISTINCT FROM OLD.dependencies THEN
        NEW.version := OLD.version + 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_behaviors_version
    BEFORE UPDATE ON behaviors
    FOR EACH ROW
    EXECUTE FUNCTION increment_behavior_version();

-- ============================================================================
-- Step 9: Update comments
-- ============================================================================

COMMENT ON TABLE behaviors IS 'API v2: Developer-defined behaviors with source code (executed in Modal Sandboxes). Renamed from actions.';
COMMENT ON TABLE companion_behavior_links IS 'API v2: Links behaviors to companions with per-companion or per-relationship config. Renamed from companion_action_links.';
COMMENT ON COLUMN companion_behavior_links.behavior_id IS 'FK to behaviors table (renamed from action_id)';
COMMENT ON COLUMN companion_behavior_links.relationship_id IS 'NULL = companion-level default, set = relationship-specific override';
COMMENT ON COLUMN companion_behavior_links.priority IS 'TRUE = orchestrator waits for completion, FALSE = background execution';
COMMENT ON COLUMN companion_behavior_links.isolated IS 'When TRUE, behavior runs in isolated container. Default FALSE for fast execution.';
COMMENT ON COLUMN companion_behavior_links.webhook_url IS 'Developer endpoint called after behavior completes';
COMMENT ON COLUMN companion_behavior_links.webhook_secret IS 'HMAC secret for webhook signature verification (X-Webhook-Signature header)';

COMMIT;
