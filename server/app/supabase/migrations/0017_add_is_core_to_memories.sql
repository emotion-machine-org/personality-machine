-- 0017_add_is_core_to_memories.sql
-- Differentiate core vs regular memories

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS is_core BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_memories_companion_core ON memories(companion_id, is_core DESC, created_at DESC);
