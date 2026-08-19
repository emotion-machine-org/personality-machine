-- Turn Context Table
-- Stores per-turn context snapshots including crafted system prompt and execution details.
-- Written asynchronously after LLM response to avoid blocking the response path.

CREATE TABLE turn_context (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Foreign keys
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,  -- NULL for test mode
    message_id UUID REFERENCES messages(id) ON DELETE SET NULL,  -- The assistant message for this turn
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,

    -- Turn identification
    turn_number INT NOT NULL,  -- 1-indexed turn within conversation

    -- Context mode
    context_mode TEXT NOT NULL DEFAULT 'raw',  -- 'raw' | 'layered'
    classifier_used BOOLEAN NOT NULL DEFAULT FALSE,

    -- The crafted system prompt (full composed prompt sent to LLM)
    system_prompt TEXT,  -- The actual system prompt composed for this turn
    system_prompt_tokens INT,  -- Token count of system prompt

    -- Execution summary (what layers ran and why)
    execution_summary JSONB,
    /*
    Example structure:
    {
        "layers": {
            "memory": {"ran": true, "source": "classifier", "classifier_decision": true, "items": 3},
            "knowledge_base": {"ran": false, "source": "not_requested", "classifier_decision": false, "reason": "classifier_skipped"},
            "tools": {"ran": true, "source": "always_run", "classifier_decision": false},
            "actions": {"ran": true, "source": "triggered", "classifier_decision": ["greet_user"], "triggered_actions": ["greet_user"]}
        },
        "classifier_used": true,
        "raw_mode": false
    }
    */

    -- Token usage breakdown
    token_usage JSONB,
    /*
    Example structure:
    {
        "system_prompt": 1200,
        "history": 450,
        "memory": 180,
        "knowledge": 0,
        "tools": 320,
        "total_input": 2150
    }
    */

    -- Timing (milliseconds)
    build_ms INT,  -- Total context build time
    classifier_ms INT,  -- Classifier inference time (if used)
    llm_ms INT,  -- LLM response time

    -- Layer-specific details (optional, for deeper debugging)
    layer_details JSONB,
    /*
    Example structure:
    {
        "memory": {
            "query": "user's message",
            "retrieved_count": 3,
            "retrieval_ms": 45
        },
        "knowledge": {
            "gated": true,
            "gate_reason": "no_keywords_matched"
        },
        "classifier": {
            "model": "gemini-2.0-flash",
            "raw_response": {...}
        }
    }
    */

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX idx_turn_context_conversation ON turn_context(conversation_id, turn_number);
CREATE INDEX idx_turn_context_companion ON turn_context(companion_id, created_at DESC);
CREATE INDEX idx_turn_context_message ON turn_context(message_id) WHERE message_id IS NOT NULL;
CREATE INDEX idx_turn_context_created ON turn_context(created_at DESC);

-- Partial index for classifier analysis
CREATE INDEX idx_turn_context_classifier ON turn_context(companion_id, created_at DESC)
    WHERE classifier_used = TRUE;

-- GIN index for JSONB queries on execution_summary
CREATE INDEX idx_turn_context_execution_gin ON turn_context USING GIN (execution_summary);

COMMENT ON TABLE turn_context IS 'Per-turn context snapshots for debugging, analytics, and audit trails. Written async after LLM response.';
COMMENT ON COLUMN turn_context.system_prompt IS 'The fully composed system prompt sent to the LLM for this turn';
COMMENT ON COLUMN turn_context.execution_summary IS 'Structured summary of which layers executed and why';
COMMENT ON COLUMN turn_context.layer_details IS 'Optional detailed debugging info per layer';
