-- Adds input modality metadata for analytics to distinguish voice vs text entries.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS input_modality TEXT;

ALTER TABLE messages
    ALTER COLUMN input_modality DROP NOT NULL;

ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_input_modality_check;

ALTER TABLE messages
    ADD CONSTRAINT messages_input_modality_check
    CHECK (input_modality IS NULL OR input_modality IN ('voice', 'text'));
