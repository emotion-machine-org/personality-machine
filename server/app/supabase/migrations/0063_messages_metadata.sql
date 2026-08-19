-- 0063_messages_metadata.sql
-- Add metadata JSONB column to messages for V3 agent mode tracking

BEGIN;

-- ============================================================================
-- Add metadata column for flexible message attributes
-- ============================================================================

-- metadata stores:
-- - For delegated user messages: {delegated: true, agent_session_id: "..."}
-- - For agent result messages: {agent_session_id: "...", turns: N, files_changed: [...]}
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Index for querying by agent_session_id
CREATE INDEX IF NOT EXISTS idx_messages_agent_session
    ON messages((metadata->>'agent_session_id'))
    WHERE metadata->>'agent_session_id' IS NOT NULL;

COMMENT ON COLUMN messages.metadata IS 'V3: Flexible metadata (agent_session_id, delegated, turns, files_changed, etc.)';

COMMIT;
