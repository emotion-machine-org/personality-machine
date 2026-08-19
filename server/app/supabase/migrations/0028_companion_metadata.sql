-- 0028_companion_metadata.sql
-- Add metadata column to companions for storing vector store identifiers and related info.

ALTER TABLE companions
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
