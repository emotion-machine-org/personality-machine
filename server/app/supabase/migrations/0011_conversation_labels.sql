-- 0011_conversation_labels.sql
-- Conversation labeling results (single latest row per conversation)

CREATE TABLE IF NOT EXISTS conversation_labels (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,

  engagement_label TEXT NOT NULL,           -- not_engaged | engaged | very_engaged
  dependency_risk_label TEXT NOT NULL,      -- no_risk | some_risk (extendable later)

  engagement_confidence NUMERIC,            -- 0..1 optional
  dependency_confidence NUMERIC,            -- 0..1 optional
  model TEXT,                               -- e.g. gemini-2.5-flash-lite
  provider TEXT,                            -- e.g. google, openai
  labels_version INT DEFAULT 1,             -- bump if taxonomy changes
  analyzed_at TIMESTAMPTZ DEFAULT now(),

  job_id UUID,                              -- optional linkage to labeling job
  status TEXT NOT NULL DEFAULT 'COMPLETED' CHECK (status IN (
    'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
  )),
  error TEXT,

  UNIQUE(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_labels_conv ON conversation_labels(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_labels_analyzed_at ON conversation_labels(analyzed_at DESC);
