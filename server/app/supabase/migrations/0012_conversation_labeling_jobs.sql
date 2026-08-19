-- 0012_conversation_labeling_jobs.sql
-- Track background labeling jobs for inline status

CREATE TABLE IF NOT EXISTS conversation_labeling_jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,

  created_at TIMESTAMPTZ DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,

  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
    'PENDING','RUNNING','COMPLETED','FAILED','CANCELLED'
  )),
  total_conversations INT DEFAULT 0,
  processed_count INT DEFAULT 0,
  error_count INT DEFAULT 0,

  model TEXT,                       -- e.g. gemini-2.5-flash-lite
  provider TEXT,                    -- e.g. google, openai
  labels_version INT DEFAULT 1,
  skip_existing BOOLEAN DEFAULT TRUE,
  since TIMESTAMPTZ,                -- only conversations updated after this time
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_label_jobs_owner ON conversation_labeling_jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_label_jobs_companion ON conversation_labeling_jobs(companion_id, created_at DESC);

-- Ensure only one active job per companion
CREATE UNIQUE INDEX IF NOT EXISTS idx_label_jobs_unique_active
  ON conversation_labeling_jobs(companion_id)
  WHERE status IN ('PENDING','RUNNING');
