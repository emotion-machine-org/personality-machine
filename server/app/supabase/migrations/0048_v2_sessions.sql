-- 0048_v2_sessions.sql
-- API v2: Sessions table for optional bounded interactions
-- Sessions are explicit, opt-in - continuous chat is the default

BEGIN;

-- ============================================================================
-- Step 1: Create v2_sessions table
-- ============================================================================

CREATE TABLE IF NOT EXISTS v2_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Parent relationship
    relationship_id UUID NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,

    -- Session metadata
    type TEXT,                                  -- 'coaching', 'therapy', custom types
    status TEXT NOT NULL DEFAULT 'active',      -- 'active' | 'ended'

    -- Isolation flag: if TRUE, session is sealed
    -- - No prior message history loaded
    -- - Memory read-only (can read, cannot write)
    -- - No state changes persist to relationship
    -- - Messages stay within session after end
    isolated BOOLEAN NOT NULL DEFAULT FALSE,

    -- Session-specific temporary state (cleared when session ends)
    state JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- AI-generated summary (created when session ends)
    summary TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,

    -- Constraints
    CONSTRAINT v2_sessions_status_check CHECK (status IN ('active', 'ended'))
);

-- ============================================================================
-- Step 2: Add FK from messages.session_id to v2_sessions
-- ============================================================================

ALTER TABLE messages
    ADD CONSTRAINT fk_messages_session
    FOREIGN KEY (session_id) REFERENCES v2_sessions(id) ON DELETE SET NULL;

-- ============================================================================
-- Step 3: Indexes
-- ============================================================================

-- List sessions for a relationship
CREATE INDEX idx_v2_sessions_relationship
    ON v2_sessions(relationship_id, created_at DESC);

-- Find active sessions for a relationship
CREATE INDEX idx_v2_sessions_relationship_active
    ON v2_sessions(relationship_id)
    WHERE status = 'active';

-- ============================================================================
-- Step 4: Trigger to prevent multiple active sessions per relationship
-- ============================================================================

CREATE OR REPLACE FUNCTION check_single_active_session()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'active' THEN
        IF EXISTS (
            SELECT 1 FROM v2_sessions
            WHERE relationship_id = NEW.relationship_id
              AND status = 'active'
              AND id != NEW.id
        ) THEN
            RAISE EXCEPTION 'Relationship already has an active session';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_v2_sessions_single_active
    BEFORE INSERT OR UPDATE ON v2_sessions
    FOR EACH ROW
    EXECUTE FUNCTION check_single_active_session();

-- ============================================================================
-- Step 5: Updated_at trigger (reuse pattern)
-- ============================================================================

ALTER TABLE v2_sessions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE OR REPLACE FUNCTION touch_v2_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_v2_sessions_updated_at
    BEFORE UPDATE ON v2_sessions
    FOR EACH ROW
    EXECUTE FUNCTION touch_v2_sessions_updated_at();

-- ============================================================================
-- Step 6: Comments
-- ============================================================================

COMMENT ON TABLE v2_sessions IS 'API v2: Optional bounded interactions within a relationship';
COMMENT ON COLUMN v2_sessions.type IS 'Session type label: coaching, therapy, or custom';
COMMENT ON COLUMN v2_sessions.status IS 'Session lifecycle: active or ended';
COMMENT ON COLUMN v2_sessions.isolated IS 'If TRUE: no history, read-only memory, no state writes, sealed record';
COMMENT ON COLUMN v2_sessions.state IS 'Session-specific temporary state (cleared on end)';
COMMENT ON COLUMN v2_sessions.summary IS 'AI-generated summary created when session ends';

COMMIT;
