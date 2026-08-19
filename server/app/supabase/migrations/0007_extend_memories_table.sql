-- 0007_extend_memories_table.sql
-- Extend memories table with importance & metadata fields

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS importance       REAL        NOT NULL DEFAULT 0.5,
    ADD COLUMN IF NOT EXISTS weight_user      REAL        NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS modality         TEXT        NOT NULL DEFAULT 'text',
    ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS commentary       TEXT,
    ADD COLUMN IF NOT EXISTS conversation_id  UUID REFERENCES conversations(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS sender_type      TEXT        NOT NULL DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS external_user_id TEXT;

-- Add indices for efficient queries
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories (last_accessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_conversation ON memories (conversation_id);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories (importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_companion_importance ON memories (companion_id, importance DESC);
