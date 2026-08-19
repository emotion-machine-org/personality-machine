-- Relationship message summaries (incremental)
-- Stores versioned summaries of conversation history for each relationship

CREATE TABLE relationship_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    relationship_id UUID NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,

    -- Summary content
    content TEXT NOT NULL,

    -- Tracking what this summary covers
    version INT NOT NULL DEFAULT 1,              -- v1, v2, v3...
    messages_start INT NOT NULL,                 -- First message seq included
    messages_end INT NOT NULL,                   -- Last message seq included
    message_count INT NOT NULL,                  -- Total messages summarized (cumulative)

    -- Metadata
    model TEXT,                                  -- LLM used for summarization
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Ensure versions are sequential per relationship
    UNIQUE(relationship_id, version)
);

-- Index for quick lookup of latest summary
CREATE INDEX idx_relationship_summaries_latest
ON relationship_summaries(relationship_id, version DESC);

-- Index for relationship cleanup
CREATE INDEX idx_relationship_summaries_relationship
ON relationship_summaries(relationship_id);

-- Track summarization state on relationship
ALTER TABLE relationships
ADD COLUMN last_summarized_at TIMESTAMPTZ,
ADD COLUMN last_summarized_message_count INT DEFAULT 0;
