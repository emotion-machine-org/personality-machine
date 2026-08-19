-- Conversation message counters and last_message_at (trigger-maintained)
-- Adds columns, backfills from messages, and creates triggers to keep them fresh.

BEGIN;

-- 1) Add columns if missing
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS message_count INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMPTZ NULL;

-- 2) Helpful index for fast last_message_at recompute on delete
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created_at_desc
  ON messages (conversation_id, created_at DESC);

-- 3) Backfill from existing messages
WITH agg AS (
  SELECT
    conversation_id,
    COUNT(*) AS cnt,
    MAX(created_at) AS last
  FROM messages
  GROUP BY conversation_id
)
UPDATE conversations c
SET message_count   = a.cnt,
    last_message_at = a.last
FROM agg a
WHERE c.id = a.conversation_id;

-- 4) Trigger function for INSERT/DELETE on messages
CREATE OR REPLACE FUNCTION update_conversation_counters_on_message_change()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- Bump count and advance last_message_at
    UPDATE conversations
      SET message_count = conversations.message_count + 1,
          last_message_at = GREATEST(
            COALESCE(conversations.last_message_at, NEW.created_at),
            COALESCE(NEW.created_at, NOW())
          )
      WHERE id = NEW.conversation_id;
    RETURN NEW;

  ELSIF TG_OP = 'DELETE' THEN
    -- Decrement and recompute last_message_at from remaining messages
    UPDATE conversations c
      SET message_count = GREATEST(0, c.message_count - 1),
          last_message_at = sub.last
      FROM (
        SELECT MAX(created_at) AS last
        FROM messages
        WHERE conversation_id = OLD.conversation_id
      ) sub
      WHERE c.id = OLD.conversation_id;
    RETURN OLD;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 5) Triggers
DROP TRIGGER IF EXISTS trg_messages_insert_update_conversation_counters ON messages;
DROP TRIGGER IF EXISTS trg_messages_delete_update_conversation_counters ON messages;

CREATE TRIGGER trg_messages_insert_update_conversation_counters
AFTER INSERT ON messages
FOR EACH ROW EXECUTE FUNCTION update_conversation_counters_on_message_change();

CREATE TRIGGER trg_messages_delete_update_conversation_counters
AFTER DELETE ON messages
FOR EACH ROW EXECUTE FUNCTION update_conversation_counters_on_message_change();

COMMIT;
