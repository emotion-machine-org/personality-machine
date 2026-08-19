-- 0034_context_engine_analytics.sql
-- Add columns to track context engine usage for A/B comparison

BEGIN;

-- Conversation-level: which engine is used for this conversation
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS context_engine TEXT;

COMMENT ON COLUMN conversations.context_engine IS 'Context engine used: layered | legacy | null (for old conversations)';

-- Message-level: build timing for assistant messages
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS build_ms INTEGER;

COMMENT ON COLUMN messages.build_ms IS 'Context build time in milliseconds (assistant messages only)';

-- Index for querying conversations by engine type
CREATE INDEX IF NOT EXISTS idx_conversations_context_engine
ON conversations(context_engine)
WHERE context_engine IS NOT NULL;

COMMIT;
