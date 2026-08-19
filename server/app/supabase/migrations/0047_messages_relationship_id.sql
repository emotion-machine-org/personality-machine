-- 0047_messages_relationship_id.sql
-- API v2: Add relationship_id, session_id, seq, and proactive message support to messages table

BEGIN;

-- ============================================================================
-- Step 1: Add relationship_id column (nullable for v1 compatibility)
-- ============================================================================

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS relationship_id UUID REFERENCES relationships(id) ON DELETE CASCADE;

-- ============================================================================
-- Step 2: Add session_id column for v2 sessions (optional bounded interactions)
-- ============================================================================

-- session_id is nullable: NULL = continuous chat, set = session-scoped message
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS session_id UUID;
-- FK will be added after v2_sessions table is created in next migration

-- ============================================================================
-- Step 3: Add seq column for event ordering within a relationship
-- ============================================================================

-- seq is per-relationship monotonically increasing sequence number
-- Used for event replay via since_seq parameter
-- NULL for v1 messages (no seq tracking)
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS seq BIGINT;

-- ============================================================================
-- Step 4: Add proactive message support columns
-- ============================================================================

-- Flag to identify proactive messages (initiated by behavior, not user)
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS is_proactive BOOLEAN NOT NULL DEFAULT FALSE;

-- Delivery status for proactive messages
-- NULL = normal message (delivered inline)
-- 'pending' = queued, waiting for user to connect
-- 'delivered' = pushed via WebSocket
-- 'acknowledged' = user confirmed receipt
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS delivery_status TEXT;

-- TTL for pending proactive messages
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Source behavior key for proactive messages
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS source_behavior_key TEXT;

-- Constraint for delivery_status values
ALTER TABLE messages
    ADD CONSTRAINT messages_delivery_status_check
    CHECK (delivery_status IS NULL OR delivery_status IN ('pending', 'delivered', 'acknowledged'));

-- ============================================================================
-- Step 5: Add indexes for v2 access patterns
-- ============================================================================

-- Primary v2 lookup: messages by relationship
CREATE INDEX IF NOT EXISTS idx_messages_relationship
    ON messages(relationship_id, created_at DESC)
    WHERE relationship_id IS NOT NULL;

-- Messages by relationship + seq for replay
CREATE INDEX IF NOT EXISTS idx_messages_relationship_seq
    ON messages(relationship_id, seq)
    WHERE relationship_id IS NOT NULL AND seq IS NOT NULL;

-- Messages by session (for session-scoped queries)
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at)
    WHERE session_id IS NOT NULL;

-- Pending proactive messages for a relationship (inbox)
CREATE INDEX IF NOT EXISTS idx_messages_proactive_pending
    ON messages(relationship_id, created_at)
    WHERE is_proactive = TRUE AND delivery_status = 'pending';

-- Cleanup expired proactive messages
CREATE INDEX IF NOT EXISTS idx_messages_proactive_expires
    ON messages(expires_at)
    WHERE is_proactive = TRUE AND delivery_status = 'pending' AND expires_at IS NOT NULL;

-- ============================================================================
-- Step 6: Sequence generator for relationship message ordering
-- ============================================================================

-- Function to get next seq for a relationship
CREATE OR REPLACE FUNCTION next_relationship_message_seq(rel_id UUID)
RETURNS BIGINT AS $$
DECLARE
    next_seq BIGINT;
BEGIN
    -- Get max seq for this relationship and add 1
    -- Uses advisory lock to prevent race conditions
    PERFORM pg_advisory_xact_lock(hashtext(rel_id::text));

    SELECT COALESCE(MAX(seq), 0) + 1
    INTO next_seq
    FROM messages
    WHERE relationship_id = rel_id;

    RETURN next_seq;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Step 7: Function to cleanup expired proactive messages
-- ============================================================================

CREATE OR REPLACE FUNCTION cleanup_expired_proactive_messages()
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM messages
    WHERE is_proactive = TRUE
      AND delivery_status = 'pending'
      AND expires_at IS NOT NULL
      AND expires_at < now();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Step 8: Update comments
-- ============================================================================

COMMENT ON COLUMN messages.relationship_id IS 'API v2: Relationship this message belongs to (NULL for v1 messages)';
COMMENT ON COLUMN messages.session_id IS 'API v2: Session this message belongs to (NULL = continuous chat)';
COMMENT ON COLUMN messages.seq IS 'API v2: Per-relationship sequence number for event replay';
COMMENT ON COLUMN messages.is_proactive IS 'API v2: TRUE if message was initiated by behavior (not user)';
COMMENT ON COLUMN messages.delivery_status IS 'API v2: Proactive message delivery: pending, delivered, acknowledged';
COMMENT ON COLUMN messages.expires_at IS 'API v2: Proactive messages expire if not delivered by this time';
COMMENT ON COLUMN messages.source_behavior_key IS 'API v2: Behavior key that created this proactive message';

COMMIT;
