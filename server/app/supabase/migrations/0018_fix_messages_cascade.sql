-- 0018_fix_messages_cascade.sql
-- Ensure messages.conversation_id has ON DELETE CASCADE to conversations(id)

DO $$
DECLARE
    fk_name text;
BEGIN
    -- Find any FK on messages(conversation_id) referencing conversations(id)
    SELECT con.conname INTO fk_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid AND rel.relname = 'messages'
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    JOIN pg_class frel ON frel.oid = con.confrelid AND frel.relname = 'conversations'
    WHERE con.contype = 'f'
      AND att.attname = 'conversation_id';

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE messages DROP CONSTRAINT %I', fk_name);
    END IF;
END $$;

ALTER TABLE messages
  ADD CONSTRAINT messages_conversation_id_fkey
  FOREIGN KEY (conversation_id)
  REFERENCES conversations(id)
  ON DELETE CASCADE;
