-- Memory V2 scratchpad entries (per relationship)
-- A flat list of semantic entries that get injected into the system prompt
-- Unlike v1's vector-based retrieval, v2 loads the entire scratchpad

CREATE TABLE memory_v2_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    relationship_id UUID NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    type            TEXT,  -- e.g., "identity", "preference", "goal", "event", "relationship", "other"
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT memory_v2_content_not_empty CHECK (length(trim(content)) > 0)
);

-- Indexes
CREATE INDEX idx_memory_v2_relationship ON memory_v2_entries(relationship_id);
CREATE INDEX idx_memory_v2_updated ON memory_v2_entries(relationship_id, updated_at DESC);

-- Trigger to update updated_at on modifications
CREATE OR REPLACE FUNCTION update_memory_v2_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_memory_v2_updated_at
    BEFORE UPDATE ON memory_v2_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_memory_v2_updated_at();

-- Soft limit check function (warns but doesn't block)
CREATE OR REPLACE FUNCTION check_memory_v2_limit()
RETURNS TRIGGER AS $$
DECLARE
    current_count INT;
    max_limit INT;
BEGIN
    -- Get limit from relationship config or default to 100
    SELECT COALESCE(
        (r.config->'memory'->>'max_entries')::INT,
        100
    ) INTO max_limit
    FROM relationships r
    WHERE r.id = NEW.relationship_id;

    SELECT COUNT(*) INTO current_count
    FROM memory_v2_entries
    WHERE relationship_id = NEW.relationship_id;

    -- Soft limit: allow insert but log warning
    IF current_count >= max_limit THEN
        RAISE WARNING 'Memory V2 soft limit reached for relationship %: % entries (limit: %)',
            NEW.relationship_id, current_count, max_limit;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_memory_v2_limit_check
    BEFORE INSERT ON memory_v2_entries
    FOR EACH ROW
    EXECUTE FUNCTION check_memory_v2_limit();

COMMENT ON TABLE memory_v2_entries IS 'Memory V2 scratchpad entries per relationship - flat list injected into system prompt';
COMMENT ON COLUMN memory_v2_entries.type IS 'Entry type: identity, preference, goal, event, relationship, other';
