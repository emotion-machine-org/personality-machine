-- 0032_companion_user_states.sql
-- State management for companion+user pairs (persists across conversations)

BEGIN;

/* ---------------------------------------------------------------------- */
/* companion_user_states: Per-user state for each companion               */
/* ---------------------------------------------------------------------- */

CREATE TABLE IF NOT EXISTS companion_user_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    external_user_id TEXT NOT NULL,

    -- EM-internal companion state (hidden from developers)
    -- Tracks the companion's "self" in relation to this user
    companion_state JSONB NOT NULL DEFAULT '{
        "energy": 1.0,
        "mood": null,
        "relationship_stage": null
    }'::jsonb,

    -- EM-defined user state (developers can read, limited write)
    -- Stores inferences about the user derived from conversations
    user_state JSONB NOT NULL DEFAULT '{
        "impressions": {},
        "inferred_traits": {},
        "sentiment_history": []
    }'::jsonb,

    -- App-defined state (full read/write for developers)
    -- This is where their user_profile variables (~50) go
    app_state JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Optimistic locking for concurrent updates
    version INT NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One state per companion+user pair
    UNIQUE(companion_id, external_user_id)
);

-- Primary lookup: get state for a specific companion+user
CREATE INDEX idx_companion_user_states_lookup
    ON companion_user_states(companion_id, external_user_id);

    -- Find all users for a companion (for bulk operations/analytics)
CREATE INDEX idx_companion_user_states_companion
    ON companion_user_states(companion_id, updated_at DESC);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION touch_companion_user_states_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companion_user_states_updated_at
    BEFORE UPDATE ON companion_user_states
    FOR EACH ROW
    EXECUTE FUNCTION touch_companion_user_states_updated_at();

-- Comments for documentation
COMMENT ON TABLE companion_user_states IS 'Per-user state for each companion, persists across all conversations';
COMMENT ON COLUMN companion_user_states.companion_state IS 'EM-internal: companion mood, energy, relationship stage (hidden from developers)';
COMMENT ON COLUMN companion_user_states.user_state IS 'EM-defined: user impressions, inferred traits (read by developers, limited write)';
COMMENT ON COLUMN companion_user_states.app_state IS 'App-defined: custom user profile variables (full developer access)';
COMMENT ON COLUMN companion_user_states.version IS 'Optimistic locking version - increment on each update';

COMMIT;
