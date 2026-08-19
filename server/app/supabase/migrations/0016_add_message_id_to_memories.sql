-- 0016_add_message_id_to_memories.sql
-- Add message_id reference to memories for provenance

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS message_id UUID REFERENCES messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_memories_message ON memories(message_id);
