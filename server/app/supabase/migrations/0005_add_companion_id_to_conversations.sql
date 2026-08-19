-- 0005_add_companion_id_to_conversations.sql
-- Add direct relationship between conversations and companions for better analytics

/* ---------- ADD COMPANION_ID TO CONVERSATIONS --------------------------- */

-- Add companion_id column to conversations table
ALTER TABLE conversations
ADD COLUMN companion_id UUID REFERENCES companions(id) ON DELETE CASCADE;

-- Backfill existing conversations with companion_id from deployments
UPDATE conversations
SET companion_id = d.companion_id
FROM deployments d
WHERE conversations.deployment_id = d.id;

-- Make companion_id NOT NULL now that we've backfilled the data
ALTER TABLE conversations
ALTER COLUMN companion_id SET NOT NULL;

/* ---------- ADD INDICES FOR ANALYTICS PERFORMANCE ---------------------- */

-- Index for analytics queries: get conversations by companion
CREATE INDEX IF NOT EXISTS idx_conversations_companion
ON conversations(companion_id, started_at DESC);

-- Index for deployments by companion (if not already exists)
CREATE INDEX IF NOT EXISTS idx_deployments_companion
ON deployments(companion_id, created_at DESC);

/* ---------- COMMENTS FOR CLARITY --------------------------------------- */

COMMENT ON COLUMN conversations.companion_id IS 'Direct reference to companion for analytics and historical data preservation';
COMMENT ON INDEX idx_conversations_companion IS 'Optimizes analytics queries for companion conversations';
