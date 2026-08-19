-- 0046_relationships_table.sql
-- API v2: Rename companion_user_states to relationships and extend schema
-- This is the core entity for the v2 API - represents a user-companion pair

BEGIN;

-- ============================================================================
-- Step 1: Rename table companion_user_states -> relationships
-- ============================================================================

ALTER TABLE companion_user_states RENAME TO relationships;

-- ============================================================================
-- Step 2: Add new columns for v2 API
-- ============================================================================

-- user_id is an alias column for external_user_id (v2 uses user_id terminology)
-- We keep external_user_id for backward compatibility and add user_id as generated column
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS user_id TEXT GENERATED ALWAYS AS (external_user_id) STORED;

-- Tracking columns
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS last_interaction_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS message_count INT NOT NULL DEFAULT 0;

-- Relationship-level config overrides (merges with companion config)
-- Uses JSON Merge Patch semantics (RFC 7396)
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Context mode for this relationship (locked after first message)
-- Values: 'legacy', 'layered', NULL (inherit from companion)
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS context_mode TEXT DEFAULT NULL;

-- Flag to lock context_mode after first message
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS context_mode_locked BOOLEAN NOT NULL DEFAULT FALSE;

-- Developer-defined metadata (not used by EM internally)
ALTER TABLE relationships
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ============================================================================
-- Step 3: Update indexes for new access patterns
-- ============================================================================

-- Primary lookup: companion + user_id (v2 terminology)
CREATE INDEX IF NOT EXISTS idx_relationships_companion_user
    ON relationships(companion_id, user_id);

-- List relationships for a companion with pagination
CREATE INDEX IF NOT EXISTS idx_relationships_companion_last_interaction
    ON relationships(companion_id, last_interaction_at DESC NULLS LAST);

-- ============================================================================
-- Step 4: Rename trigger and function to match new table name
-- ============================================================================

-- Drop old trigger
DROP TRIGGER IF EXISTS trg_companion_user_states_updated_at ON relationships;

-- Create/replace function with new name
CREATE OR REPLACE FUNCTION touch_relationships_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create new trigger
CREATE TRIGGER trg_relationships_updated_at
    BEFORE UPDATE ON relationships
    FOR EACH ROW
    EXECUTE FUNCTION touch_relationships_updated_at();

-- ============================================================================
-- Step 5: Function to lock context_mode after first message
-- ============================================================================

CREATE OR REPLACE FUNCTION lock_relationship_context_mode()
RETURNS TRIGGER AS $$
BEGIN
    -- If context_mode is being changed and already locked, reject
    IF OLD.context_mode_locked = TRUE
       AND NEW.context_mode IS DISTINCT FROM OLD.context_mode THEN
        RAISE EXCEPTION 'Cannot change context_mode after relationship has messages';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_relationships_context_mode_lock
    BEFORE UPDATE ON relationships
    FOR EACH ROW
    EXECUTE FUNCTION lock_relationship_context_mode();

-- ============================================================================
-- Step 6: Update comments
-- ============================================================================

COMMENT ON TABLE relationships IS 'API v2: User-companion pairs with persistent state (renamed from companion_user_states)';
COMMENT ON COLUMN relationships.user_id IS 'User identifier (generated from external_user_id for v2 API)';
COMMENT ON COLUMN relationships.external_user_id IS 'Original user identifier (kept for v1 compatibility)';
COMMENT ON COLUMN relationships.companion_state IS 'EM-internal: companion mood, energy, relationship stage';
COMMENT ON COLUMN relationships.user_state IS 'EM-defined: user impressions, inferred traits';
COMMENT ON COLUMN relationships.app_state IS 'Developer-defined: custom state (full developer access)';
COMMENT ON COLUMN relationships.config IS 'Relationship-level config overrides (JSON Merge Patch with companion config)';
COMMENT ON COLUMN relationships.context_mode IS 'Context engine mode: legacy, layered, or NULL (inherit from companion)';
COMMENT ON COLUMN relationships.context_mode_locked IS 'TRUE after first message - prevents context_mode changes';
COMMENT ON COLUMN relationships.last_interaction_at IS 'Timestamp of last message in this relationship';
COMMENT ON COLUMN relationships.message_count IS 'Total messages in this relationship';
COMMENT ON COLUMN relationships.metadata IS 'Developer-defined metadata (not used by EM)';

COMMIT;
