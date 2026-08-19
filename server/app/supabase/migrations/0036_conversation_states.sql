-- 0033_conversation_states.sql
-- State management for individual conversations (ephemeral, per-conversation)

BEGIN;

/* ---------------------------------------------------------------------- */
/* conversation_states: Per-conversation state (resets on new conversation) */
/* ---------------------------------------------------------------------- */

CREATE TABLE IF NOT EXISTS conversation_states (
    -- Primary key is the conversation_id itself (1:1 relationship)
    conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,

    -- Topic tracking for this conversation
    topic_state JSONB NOT NULL DEFAULT '{
        "current_topic": null,
        "topic_stack": [],
        "topic_history": [],
        "topic_confidence": null
    }'::jsonb,

    -- Turn counter for this conversation
    turn_count INT NOT NULL DEFAULT 0,

    -- Misc conversation-scoped data (extensible)
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION touch_conversation_states_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_conversation_states_updated_at
    BEFORE UPDATE ON conversation_states
    FOR EACH ROW
    EXECUTE FUNCTION touch_conversation_states_updated_at();

-- Comments for documentation
COMMENT ON TABLE conversation_states IS 'Per-conversation state, resets when a new conversation starts';
COMMENT ON COLUMN conversation_states.topic_state IS 'Current topic, topic stack, and topic history for this conversation';
COMMENT ON COLUMN conversation_states.turn_count IS 'Number of turns (user+assistant pairs) in this conversation';
COMMENT ON COLUMN conversation_states.metadata IS 'Extensible metadata for conversation-scoped variables';

COMMIT;
