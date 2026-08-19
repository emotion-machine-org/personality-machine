-- 0019_memories_content_nullable_and_index.sql
-- Make memory content nullable (allow message-ref-only memories) and add per-user index.

ALTER TABLE memories
  ALTER COLUMN content DROP NOT NULL;

-- Fast per-user retrieval ordered by recency
CREATE INDEX IF NOT EXISTS idx_memories_companion_user
  ON memories (companion_id, external_user_id, last_accessed_at DESC);
