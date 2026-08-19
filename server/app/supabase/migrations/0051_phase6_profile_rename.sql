-- 0051_phase6_profile_rename.sql
-- Phase 6: State & Sensing Model
-- Renames app_state -> profile, drops unused user_state/companion_state columns

BEGIN;

-- ============================================================================
-- Step 1: Rename app_state column to profile
-- ============================================================================

ALTER TABLE relationships RENAME COLUMN app_state TO profile;

-- ============================================================================
-- Step 2: Drop unused user_state and companion_state columns
-- These were EM-internal state fields that are no longer used in v2 API
-- ============================================================================

ALTER TABLE relationships DROP COLUMN IF EXISTS user_state;
ALTER TABLE relationships DROP COLUMN IF EXISTS companion_state;

-- ============================================================================
-- Step 3: Update comments
-- ============================================================================

COMMENT ON COLUMN relationships.profile IS 'Developer-controlled structured state per relationship (formerly app_state)';

-- ============================================================================
-- Step 4: Add function to get/set session state
-- (for PostTurnExecutor to use when writing session state effects)
-- ============================================================================

-- Function to patch session state (JSON Merge Patch)
CREATE OR REPLACE FUNCTION patch_session_state(
    p_session_id UUID,
    p_changes JSONB
) RETURNS JSONB AS $$
DECLARE
    v_result JSONB;
BEGIN
    UPDATE v2_sessions
    SET state = state || p_changes
    WHERE id = p_session_id
    RETURNING state INTO v_result;

    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

-- Function to check if session is isolated (prevents state writes)
CREATE OR REPLACE FUNCTION is_session_isolated(p_session_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    v_isolated BOOLEAN;
BEGIN
    SELECT isolated INTO v_isolated
    FROM v2_sessions
    WHERE id = p_session_id;

    RETURN COALESCE(v_isolated, FALSE);
END;
$$ LANGUAGE plpgsql;

COMMIT;
