-- Store sanitized render-friendly versions of assistant messages

BEGIN;

CREATE TABLE IF NOT EXISTS message_display (
    message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    sanitized_content TEXT,
    provider TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
