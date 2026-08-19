-- 0009_add_session_management.sql
-- Move session management from in-memory to database

/* Add session state tracking to conversations table */
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS session_state TEXT DEFAULT 'voice_active';

/* Add session tracking table for active WebSocket sessions */
CREATE TABLE IF NOT EXISTS active_sessions (
    session_id TEXT PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    websocket_state TEXT NOT NULL DEFAULT 'connecting', -- connecting, active, ended
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

/* Function to automatically update updated_at */
CREATE OR REPLACE FUNCTION update_session_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

/* Trigger for auto-updating timestamps */
CREATE TRIGGER update_active_sessions_updated_at
    BEFORE UPDATE ON active_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_session_timestamp();

/* Index for efficient session lookups */
CREATE INDEX IF NOT EXISTS idx_active_sessions_conversation ON active_sessions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_session_state ON conversations(session_state);

/* Optional: Clean up old sessions (could be run periodically) */
CREATE OR REPLACE FUNCTION cleanup_old_sessions(older_than_hours INTEGER DEFAULT 24)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM active_sessions
    WHERE updated_at < (now() - interval '1 hour' * older_than_hours);

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
