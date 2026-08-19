-- 0014_background_jobs_privacy_sharing.sql
-- Unify background jobs, add message-level redaction spans, and sharing flags.

BEGIN;

/* ---------------------------------------------------------------------- */
/* 1) Unified background jobs table (replaces conversation_labeling_jobs) */
/* ---------------------------------------------------------------------- */

CREATE TABLE IF NOT EXISTS background_jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  job_type TEXT NOT NULL CHECK (job_type IN (
    'label_conversations',
    'privacy_redaction',
    'transcription',
    'synthetic_data',
    'simulation'
  )),

  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
    'PENDING','RUNNING','COMPLETED','FAILED','CANCELLED'
  )),

  companion_id UUID NULL REFERENCES companions(id) ON DELETE CASCADE,
  conversation_id UUID NULL REFERENCES conversations(id) ON DELETE CASCADE,

  created_at TIMESTAMPTZ DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,

  error TEXT,
  params JSONB,                 -- generic bag: model/provider/skip_existing/since/etc.

  -- generic progress fields
  total_items INT,
  processed_count INT,
  error_count INT,

  -- optional freshness snapshot for redaction
  source_last_message_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bg_jobs_owner ON background_jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_type_status ON background_jobs(job_type, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_companion ON background_jobs(companion_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_conversation ON background_jobs(conversation_id, created_at DESC);

-- one active job per (job_type, companion) when companion-scoped
CREATE UNIQUE INDEX IF NOT EXISTS idx_bg_jobs_active_companion
  ON background_jobs(job_type, companion_id)
  WHERE companion_id IS NOT NULL AND status IN ('PENDING','RUNNING');

-- one active job per (job_type, conversation) when conversation-scoped
CREATE UNIQUE INDEX IF NOT EXISTS idx_bg_jobs_active_conversation
  ON background_jobs(job_type, conversation_id)
  WHERE conversation_id IS NOT NULL AND status IN ('PENDING','RUNNING');

COMMENT ON TABLE background_jobs IS 'Unified job table for labeling, privacy redaction, transcription, synthetic data, and simulation.';
COMMENT ON COLUMN background_jobs.params IS 'Free-form JSON for job-specific parameters.';

/* Backfill from conversation_labeling_jobs into background_jobs */
INSERT INTO background_jobs (
  id, owner_id, job_type, status, companion_id,
  created_at, started_at, completed_at, error,
  params, total_items, processed_count, error_count
)
SELECT
  lj.id,
  lj.owner_id,
  'label_conversations'::text AS job_type,
  lj.status,
  lj.companion_id,
  lj.created_at,
  lj.started_at,
  lj.completed_at,
  lj.error,
  jsonb_build_object(
    'model', lj.model,
    'provider', lj.provider,
    'labels_version', lj.labels_version,
    'skip_existing', lj.skip_existing,
    'since', lj.since
  ) AS params,
  lj.total_conversations AS total_items,
  lj.processed_count,
  lj.error_count
FROM conversation_labeling_jobs lj
ON CONFLICT (id) DO NOTHING;

/* ---------------------------------------------------------------------- */
/* 2) Extend messages with redaction metadata (spans only, no copies)     */
/* ---------------------------------------------------------------------- */

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS pii_spans JSONB,
  ADD COLUMN IF NOT EXISTS redacted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS redaction_version INT NOT NULL DEFAULT 1;

-- Help staleness checks and range scans
CREATE INDEX IF NOT EXISTS idx_messages_conv_redacted ON messages(conversation_id, redacted_at);

COMMENT ON COLUMN messages.pii_spans IS 'Array of PII spans per message, e.g., [{"label":"EMAIL","start":10,"end":22}]';
COMMENT ON COLUMN messages.redacted_at IS 'Timestamp when PII spans were computed for this message (null if never processed).';
COMMENT ON COLUMN messages.redaction_version IS 'Version of redaction taxonomy/policy applied.';

/* ---------------------------------------------------------------------- */
/* 3) Conversation sharing flags                                           */
/* ---------------------------------------------------------------------- */

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS share_status TEXT NOT NULL DEFAULT 'private' CHECK (share_status IN ('private','public')),
  ADD COLUMN IF NOT EXISTS share_slug TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS share_enabled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS share_title TEXT;

CREATE INDEX IF NOT EXISTS idx_conversations_share_status ON conversations(share_status);

COMMENT ON COLUMN conversations.share_status IS 'Sharing state for public, redacted view-only page (private|public).';
COMMENT ON COLUMN conversations.share_slug IS 'Unguesable slug for public share URL when share_status=public.';

COMMIT;
