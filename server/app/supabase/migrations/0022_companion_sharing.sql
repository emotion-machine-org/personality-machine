-- Companion sharing tables and columns

BEGIN;

-- ---------------------------------------------------------------------------
-- companion_shares: metadata + controls for public sharing
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companion_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version_id UUID NULL REFERENCES companion_versions(id) ON DELETE SET NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'draft',
    allow_text BOOLEAN NOT NULL DEFAULT TRUE,
    allow_voice BOOLEAN NOT NULL DEFAULT FALSE,
    require_auth BOOLEAN NOT NULL DEFAULT FALSE,
    config_snapshot JSONB NULL,
    display_name TEXT NULL,
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ NULL,
    disabled_at TIMESTAMPTZ NULL,
    total_sessions BIGINT NOT NULL DEFAULT 0,
    total_messages BIGINT NOT NULL DEFAULT 0,
    total_voice_sessions BIGINT NOT NULL DEFAULT 0,
    last_activity_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_companion_shares_companion ON companion_shares(companion_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_shares_slug ON companion_shares(slug);

-- Shared function for updated_at maintenance
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_companion_shares_touch ON companion_shares;
CREATE TRIGGER trg_companion_shares_touch
BEFORE UPDATE ON companion_shares
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ---------------------------------------------------------------------------
-- conversations: link to share + visitor token hashing
-- ---------------------------------------------------------------------------
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS share_id UUID NULL REFERENCES companion_shares(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS share_token_hash BYTEA NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_share_started_at
    ON conversations(share_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_share_token
    ON conversations(share_id, share_token_hash);

-- ---------------------------------------------------------------------------
-- companion_share_sessions: per-visitor analytics and rate limiting
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companion_share_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    share_id UUID NOT NULL REFERENCES companion_shares(id) ON DELETE CASCADE,
    conversation_id UUID NULL REFERENCES conversations(id) ON DELETE SET NULL,
    visitor_token_hash BYTEA NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    message_count BIGINT NOT NULL DEFAULT 0,
    voice_sessions_started BIGINT NOT NULL DEFAULT 0,
    windowed_message_counts JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_share_sessions_unique_visitor
    ON companion_share_sessions(share_id, visitor_token_hash);

CREATE INDEX IF NOT EXISTS idx_share_sessions_last_seen
    ON companion_share_sessions(share_id, last_seen_at DESC);

DROP TRIGGER IF EXISTS trg_companion_share_sessions_touch ON companion_share_sessions;
CREATE TRIGGER trg_companion_share_sessions_touch
BEFORE UPDATE ON companion_share_sessions
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;
